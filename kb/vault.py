"""Vault discovery, the registry, the access guard, and record loading/lookup (spec §2.3)."""

import os
from functools import cached_property
from pathlib import Path

import yaml

from kb.profile import Profile, load_profile
from kb.records import Record, link_target_name, read_record
from kb.util import KBError

EXCLUDED_DIRS = {".kb", ".git", ".obsidian", "originals", "text", "inbox", "templates", "views", "intake"}
EXEMPT_FILES = {"AGENTS.md", "Home.md", "README.md", "log.md"}


# --- registry -------------------------------------------------------------------

def registry_path() -> Path:
    return Path(os.environ.get("KB_REGISTRY") or Path.home() / ".config" / "kb" / "vaults.yaml")


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"vaults": {}, "repos": {}}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KBError(f"registry {p} is not valid YAML: {str(e).splitlines()[0]}") from None
    data.setdefault("vaults", {})
    data.setdefault("repos", {})
    return data


def expand(path) -> Path:
    return Path(os.path.expanduser(str(path))).resolve()


# --- discovery --------------------------------------------------------------------

def find_root(start=None) -> Path:
    """Nearest ancestor with kb.yaml, or <dir>/knowledge-base/kb.yaml when run from a repo root."""
    here = Path(start or os.getcwd()).resolve()
    for d in (here, *here.parents):
        if (d / "kb.yaml").is_file():
            return d
        if (d / "knowledge-base" / "kb.yaml").is_file():
            return d / "knowledge-base"
    raise KBError(f"no kb.yaml found from {here} upward; cd into a vault, pass --vault NAME, or run `kb init`")


def open_vault(name: str | None = None, start=None, check_access: bool = True) -> "Vault":
    if name:
        reg = load_registry()["vaults"]
        if name not in reg:
            raise KBError(f"no vault {name!r} in {registry_path()}; add it with `kb registry add {name} PATH`")
        root = expand(reg[name]["path"])
        if not (root / "kb.yaml").is_file():
            raise KBError(f"registry vault {name!r} points at {root}, which has no kb.yaml")
    else:
        root = find_root(start)
    v = Vault(root)
    if check_access:
        v.check_access()
    return v


def open_from_args(args, check_access: bool = True) -> "Vault":
    return open_vault(getattr(args, "vault", None), check_access=check_access)


# --- the vault --------------------------------------------------------------------

class Vault:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.profile: Profile = load_profile(self.root)

    @property
    def name(self) -> str:
        return self.profile.name or self.root.name

    @property
    def kb_dir(self) -> Path:
        d = self.root / ".kb"
        d.mkdir(exist_ok=True)
        return d

    def check_access(self) -> None:
        if self.profile.access == "local" and os.environ.get("KB_AGENT") != "local":
            raise KBError(f"vault {self.name!r} is local-only (access: local). Only local-model agents may "
                          f"read it; their runner sets KB_AGENT=local. Don't set it in a cloud agent session.")

    def rel(self, path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    # --- files and records ---

    def iter_markdown(self):
        """Every .md under the vault outside excluded dirs (and not exempt root files)."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            d = Path(dirpath)
            dirnames[:] = sorted(n for n in dirnames if not (d == self.root and n in EXCLUDED_DIRS)
                                 and not n.startswith("."))
            for fn in sorted(filenames):
                if fn.endswith(".md") and not (d == self.root and fn in EXEMPT_FILES):
                    yield d / fn

    @cached_property
    def load_errors(self) -> list:
        self.records  # populates
        return self._load_errors

    @cached_property
    def records(self) -> list[Record]:
        out, errs = [], []
        for p in self.iter_markdown():
            try:
                r = read_record(p, rel=self.rel(p))
            except KBError as e:
                errs.append((self.rel(p), str(e)))
                continue
            if r is not None:
                out.append(r)
        self._load_errors = errs
        return out

    def reload(self) -> None:
        for k in ("records", "load_errors", "by_name", "files_by_name"):
            self.__dict__.pop(k, None)

    @cached_property
    def by_name(self) -> dict:
        """casefolded id or alias -> Record (first wins; lint reports duplicates)."""
        m = {}
        for r in self.records:
            m.setdefault(r.id.casefold(), r)
        for r in self.records:
            for a in r.fm.get("aliases") or []:
                m.setdefault(str(a).casefold(), r)
        return m

    @cached_property
    def files_by_name(self) -> dict:
        """casefolded basename -> path for every file in the vault (attachments), excluding .kb/.git/inbox."""
        m = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            top = Path(dirpath) == self.root
            dirnames[:] = [n for n in dirnames if n not in (".kb", ".git") and not (top and n == "inbox")]
            for fn in filenames:
                m.setdefault(fn.casefold(), Path(dirpath) / fn)
        return m

    def get(self, rid: str) -> Record | None:
        return self.by_name.get(link_target_name(rid).casefold())

    def require(self, rid: str) -> Record:
        r = self.get(rid)
        if r is None:
            raise KBError(f"no record {rid!r} in vault {self.name!r}; try `kb find` or `kb grep`")
        return r

    def resolve(self, target: str):
        """A wikilink target -> Record, or Path of an attachment, or None."""
        r = self.get(target)
        if r is not None:
            return r
        return self.files_by_name.get(link_target_name(target).casefold())

    def typedef(self, rec: Record):
        return self.profile.types.get(rec.type)

    def of_type(self, *names) -> list[Record]:
        return [r for r in self.records if r.type in names]

    # --- lookups for dedup ---

    def find_by_sha(self, sha: str) -> Record | None:
        for r in self.records:
            for o in r.fm.get("originals") or []:
                if isinstance(o, dict) and o.get("sha256") == sha:
                    return r
        return None

    def find_by_url(self, url: str) -> Record | None:
        norm = norm_url(url)
        for r in self.records:
            for k in ("url", "given_url"):
                if r.fm.get(k) and norm_url(str(r.fm[k])) == norm:
                    return r
            for o in r.fm.get("originals") or []:
                if isinstance(o, dict) and o.get("fetched_url") and norm_url(str(o["fetched_url"])) == norm:
                    return r
        return None

    def new_id(self, base: str) -> str:
        """base, or base-2, base-3 ... so that it is unused."""
        rid, n = base, 2
        while rid.casefold() in self.by_name or self.files_by_name.get(f"{rid}.md".casefold()):
            rid, n = f"{base}-{n}", n + 1
        return rid


def norm_url(url: str) -> str:
    u = url.strip()
    for p in ("https://", "http://"):
        if u.lower().startswith(p):
            u = u[len(p):]
    u = u.split("#", 1)[0].rstrip("/")
    if u.lower().startswith("www."):
        u = u[4:]
    return u.lower()


_norm_url = norm_url  # backward-compatible name
