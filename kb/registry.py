"""kb registry (vaults and repos in ~/.config/kb/vaults.yaml) and kb doctor (spec §2.3, §2.8)."""

import importlib.util
import os
import shutil
from pathlib import Path

import yaml

from kb.util import KBError, atomic_write, emit
from kb.vault import expand, load_registry, open_from_args, registry_path

OWNERS = ("personal", "club", "school", "work", "third-party")
PY_DEPS = [("trafilatura", ["trafilatura"], "pip install trafilatura"),
           ("pymupdf", ["pymupdf", "fitz"], "pip install pymupdf"),
           ("markitdown", ["markitdown"], "pip install markitdown"),
           ("Pillow", ["PIL"], "pip install Pillow")]
BIN_DEPS = [("yt-dlp", "pip install yt-dlp"), ("tesseract", "apt install tesseract-ocr"), ("git", "apt install git"),
            ("rg", "apt install ripgrep"), ("node", "apt install nodejs")]


def _save(data) -> None:
    atomic_write(registry_path(), yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _tilde(path) -> str:
    p, home = expand(path), Path.home()
    return "~/" + p.relative_to(home).as_posix() if p.is_relative_to(home) and p != home else str(p)


def _vault_access(path) -> str | None:
    f = expand(path) / "kb.yaml"
    if not f.is_file():
        return None
    try:
        return str((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("access", "shared"))
    except yaml.YAMLError:
        return None


def cmd_list(args):
    reg = load_registry()
    vaults = [{"name": n, **e, "exists": (expand(e.get("path", "")) / "kb.yaml").is_file()} for n, e in reg["vaults"].items()]
    repos = [{"name": n, **e, "exists": expand(e.get("path", "")).is_dir()} for n, e in reg["repos"].items()]

    def text(d):
        lines = [f"registry: {registry_path()}", "vaults:"]
        lines += [f"  {v['name']} · {v.get('access', 'shared')} · {v.get('path')}{'' if v['exists'] else ' (missing)'}"
                  for v in d["vaults"]] or ["  (none; kb registry add NAME PATH)"]
        lines.append("repos:")
        lines += [f"  {r['name']} → {r.get('vault')} · {r.get('owner', '')} · {r.get('path')}"
                  f"{'' if r['exists'] else ' (missing)'} · docs: {', '.join(r.get('docs') or [])}" for r in d["repos"]] \
            or ["  (none)"]
        return lines
    emit(args, {"path": str(registry_path()), "vaults": vaults, "repos": repos}, text)
    return 0


def cmd_add(args):
    found = _vault_access(args.path)
    if args.access and found and args.access != found:
        raise KBError(f"{args.path}/kb.yaml says access: {found}; edit kb.yaml or drop --access")
    if not found and not (expand(args.path) / "kb.yaml").is_file():
        print(f"kb registry: note: {expand(args.path)} has no kb.yaml yet (kb init it)")
    reg = load_registry()
    entry = reg["vaults"].get(args.name) or {}
    entry.update(path=_tilde(args.path), access=args.access or found or "shared")
    reg["vaults"][args.name] = entry
    _save(reg)
    emit(args, {"name": args.name, **entry}, f"vault {args.name}: {entry['path']} ({entry['access']})")
    return 0


def cmd_add_repo(args):
    reg = load_registry()
    if not args.vault:
        raise KBError("add-repo needs --vault V: the registered vault that indexes this repo's docs")
    if args.vault not in reg["vaults"]:
        raise KBError(f"no vault {args.vault!r} in the registry; add it first with `kb registry add {args.vault} PATH`")
    entry = reg["repos"].get(args.name) or {}
    entry.update(path=_tilde(args.path), vault=args.vault)
    if args.docs:
        entry["docs"] = list(args.docs)
    entry.setdefault("docs", ["README.md", "docs/**/*.md"])
    if args.owner:
        entry["owner"] = args.owner
    reg["repos"][args.name] = entry
    _save(reg)
    emit(args, {"name": args.name, **entry}, f"repo {args.name}: {entry['path']} → vault {args.vault}")
    return 0


def cmd_remove(args):
    reg = load_registry()
    hit = [k for k in ("vaults", "repos") if reg[k].pop(args.name, None) is not None]
    if not hit:
        raise KBError(f"no vault or repo {args.name!r} in {registry_path()}; see `kb registry list`")
    _save(reg)
    emit(args, {"name": args.name, "removed_from": hit}, f"removed {args.name} from {', '.join(hit)}")
    return 0


def cmd_doctor(args):
    deps = [{"name": n, "ok": any(importlib.util.find_spec(m) for m in mods), "hint": h} for n, mods, h in PY_DEPS]
    deps += [{"name": n, "ok": bool(shutil.which(n)), "hint": h} for n, h in BIN_DEPS]
    reg = load_registry()
    entries = [{"kind": k[:-1], "name": n, "path": e.get("path"),
                "exists": expand(e.get("path", "")).exists()} for k in ("vaults", "repos") for n, e in reg[k].items()]
    try:
        v = open_from_args(args, check_access=False)
        cur = {"name": v.name, "root": str(v.root), "access": v.profile.access,
               "readable": v.profile.access != "local" or os.environ.get("KB_AGENT") == "local"}
    except KBError as e:
        cur = {"error": str(e)}
    data = {"deps": deps, "registry": str(registry_path()), "registry_exists": registry_path().exists(),
            "entries": entries, "vault": cur}

    def text(d):
        lines = [f"{'ok ' if x['ok'] else '-- '} {x['name']}" + ("" if x["ok"] else f"  (optional; {x['hint']})")
                 for x in d["deps"]]
        lines.append(f"registry: {d['registry']}" + ("" if d["registry_exists"] else " (not created yet)"))
        lines += [f"  {e['kind']} {e['name']}: {e['path']}" + ("" if e["exists"] else " (missing)") for e in d["entries"]]
        c = d["vault"]
        lines.append(f"vault: {c['error']}" if "error" in c else
                     f"vault: {c['name']} at {c['root']} · access {c['access']}"
                     + ("" if c["readable"] else " · this agent may not read it (KB_AGENT is not local)"))
        return lines
    emit(args, data, text)
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("registry", parents=[common], help="list or edit registered vaults and repos")
    sub = p.add_subparsers(dest="registry_cmd", metavar="SUBCOMMAND", required=True)
    sub.add_parser("list", parents=[common], help="registered vaults and repos").set_defaults(func=cmd_list)
    a = sub.add_parser("add", parents=[common], help="register a vault")
    a.add_argument("name", metavar="NAME")
    a.add_argument("path", metavar="PATH")
    a.add_argument("--access", choices=("shared", "local"), help="default: the vault's kb.yaml, else shared")
    a.set_defaults(func=cmd_add)
    r = sub.add_parser("add-repo", parents=[common], help="register a code repo whose docs a vault indexes")
    r.add_argument("name", metavar="NAME")
    r.add_argument("path", metavar="PATH")
    r.add_argument("--docs", nargs="+", metavar="GLOB")
    r.add_argument("--owner", choices=OWNERS)
    r.set_defaults(func=cmd_add_repo)  # --vault (from common) names the vault that indexes the repo
    rm = sub.add_parser("remove", parents=[common], help="unregister a vault or repo")
    rm.add_argument("name", metavar="NAME")
    rm.set_defaults(func=cmd_remove)
    d = subparsers.add_parser("doctor", parents=[common], help="report optional dependencies and the registry")
    d.set_defaults(func=cmd_doctor)
