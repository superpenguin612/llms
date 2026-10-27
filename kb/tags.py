"""`kb tags`: the controlled tag vocabulary. Tags are added deliberately, each with a meaning."""

import re

import yaml

from kb.profile import parse_profile
from kb.util import KBError, atomic_write, emit
from kb.vault import open_from_args

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def counts(vault) -> dict:
    c = {}
    for r in vault.records:
        for t in r.fm.get("tags") or []:
            c[str(t)] = c.get(str(t), 0) + 1
    return c


def list_tags(args) -> int:
    v = open_from_args(args)
    meanings, used = v.profile.tag_meanings, counts(v)
    rows = [{"tag": t, "meaning": meanings.get(t, ""), "records": used.get(t, 0), "defined": t in meanings}
            for t in sorted(set(meanings) | set(used))]
    lines = [f"{r['tag']} ({r['records']}){'' if r['defined'] else '  [NOT DEFINED]'} — {r['meaning']}" for r in rows]
    if v.profile.tags is None:
        lines.insert(0, "tags are not controlled in this vault (no `tags:` in kb.yaml); add `tags: {}` to start")
    emit(args, rows, lines or "no tags defined; add one with: kb tags add NAME \"meaning\"")
    return 0


def add_tag(vault_root, name: str, meaning: str) -> None:
    """Insert `  name: "meaning"` into kb.yaml's tags mapping, keeping the rest of the file (and comments)."""
    if not _NAME.match(name):
        raise KBError(f"tag {name!r}: use lowercase letters, digits and hyphens, e.g. 'active-directory'")
    if not meaning.strip():
        raise KBError("a tag needs a meaning, so others pick it correctly: kb tags add NAME \"what it covers\"")
    path = vault_root / "kb.yaml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    tags = data.get("tags")
    if isinstance(tags, list):
        raise KBError("kb.yaml `tags:` is a plain list; convert it to `name: \"meaning\"` pairs first")
    if isinstance(tags, dict) and name in tags:
        raise KBError(f"tag {name!r} already exists: {tags[name]}")
    entry = f"  {name}: {yaml.safe_dump(meaning.strip(), default_style=chr(34)).strip()}"
    lines = text.splitlines()
    i = next((n for n, l in enumerate(lines) if re.match(r"tags\s*:", l)), None)
    if i is None:
        lines += ["", "tags:", entry]
    else:
        head, _, comment = lines[i].partition("#")
        if head.split(":", 1)[1].strip():  # inline value such as `tags: {}`: replace it with a block
            lines[i:i + 1] = ["tags:" + (f"  #{comment}" if comment else ""), entry]
        else:  # block mapping: append after its last indented line
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, entry)
    new = "\n".join(lines) + "\n"
    parse_profile(yaml.safe_load(new))  # never write a broken profile
    if name not in (yaml.safe_load(new).get("tags") or {}):
        raise KBError("couldn't insert the tag automatically; add it under `tags:` in kb.yaml by hand")
    atomic_write(path, new)


def add(args) -> int:
    v = open_from_args(args)
    add_tag(v.root, args.name, args.meaning)
    emit(args, {"tag": args.name, "meaning": args.meaning}, f"added tag {args.name!r}")
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("tags", parents=[common], help="list or deliberately add controlled tags")
    sub = p.add_subparsers(dest="tags_cmd", metavar="ACTION")
    l = sub.add_parser("list", parents=[common], help="tags with meanings and record counts (default)")
    l.set_defaults(func=list_tags)
    a = sub.add_parser("add", parents=[common], help="add a tag; a meaning is required")
    a.add_argument("name")
    a.add_argument("meaning")
    a.set_defaults(func=add)
    p.set_defaults(func=list_tags)
