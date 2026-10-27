"""`kb init`: create a vault from a starter profile."""

import json
import re
import shutil
from pathlib import Path

from kb import build
from kb.profile import TEMPLATES, parse_profile, starter_names, starter_text
from kb.records import dump_front_matter
from kb.util import KBError, append_log, atomic_write, emit
from kb.vault import Vault

import yaml


def note_template(vault, td) -> str:
    """An Obsidian template (templates/<type>.md) with the keys this type's traits need."""
    fm = {"type": td.name, "title": "{{title}}", "description": "", "status": "active", "tags": []}
    for k in td.required:
        fm.setdefault(k, "")
    if td.origin == "external":
        if vault.profile.tiers:
            fm["tier"] = ""
        fm.update({"author": [], "url": "", "accessed": "{{date}}"})
    else:
        fm["updated"] = "{{date}}"
    if td.original == "immutable":
        fm.update({"originals": [], "originals_note": ""})
    if td.tracked == "refresh":
        fm["refresh"] = td.refresh
    if td.tracked == "watch":
        fm.update({"watch": [], "watch_every": td.watch_every})
    sections = ["Summary", "Key facts", "Excerpts", "Notes"] if td.quotes == "verbatim" else ["Notes"]
    body = "\n# {{title}}\n\n" + "\n\n".join(f"## {s}\n" for s in sections)
    return dump_front_matter(fm) + body


def init(target, profile: str = "generic", name: str | None = None) -> Vault:
    root = Path(target).expanduser().resolve()
    if (root / "kb.yaml").exists():
        raise KBError(f"{root / 'kb.yaml'} already exists; edit it, or pick an empty directory")
    if profile not in starter_names():
        raise KBError(f"no starter profile {profile!r}; choose one of {', '.join(starter_names())}")
    name = name or (root.parent.name if root.name == "knowledge-base" else root.name)
    text = re.sub(r"(?m)^name: .*$", f"name: {json.dumps(name)}", starter_text(profile), count=1)
    parse_profile(yaml.safe_load(text))  # validate before writing anything
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "kb.yaml", text)
    v = Vault(root)

    for td in v.profile.types.values():
        (root / td.folder_prefix).mkdir(parents=True, exist_ok=True)
        tp = root / "templates" / f"{td.name}.md"
        if not tp.exists():
            atomic_write(tp, note_template(v, td))
    for d in ("inbox", "originals", "text"):
        (root / d).mkdir(exist_ok=True)

    build.write_agents(v)
    if not (root / "Home.md").exists():
        desc = v.profile.get("description") or ""
        atomic_write(root / "Home.md", f"# {name}\n\n{desc}\n\nHow this vault works: [[AGENTS]]. "
                                       f"Catalogues and Bases views are in `views/` after `kb build`.\n")
    gi = root / ".gitignore"
    old = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".kb/" not in old.split():
        atomic_write(gi, old + ("" if not old or old.endswith("\n") else "\n") + ".kb/\n")
    for f in (TEMPLATES / "obsidian").iterdir():
        dst = root / ".obsidian" / f.name
        if not dst.exists():
            dst.parent.mkdir(exist_ok=True)
            shutil.copyfile(f, dst)
    append_log(root, "init", name, f"profile {profile}")
    return v


def run(args) -> int:
    v = init(args.dir, args.profile, args.name)
    emit(args, {"root": str(v.root), "name": v.name, "types": sorted(v.profile.types)},
         f"initialized vault {v.name!r} at {v.root} ({len(v.profile.types)} types); next: edit kb.yaml, then `kb build`")
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("init", parents=[common], help="create a vault from a starter profile")
    p.add_argument("dir", nargs="?", default=".", help="vault directory (default: current directory)")
    p.add_argument("--profile", default="generic", help=f"starter profile: {', '.join(starter_names())}")
    p.add_argument("--name", help="vault name (default: the directory name)")
    p.set_defaults(func=run)
