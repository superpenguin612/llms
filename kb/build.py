"""`kb build`: the SQLite index, Obsidian Bases views, static catalogues and the AGENTS.md block."""

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

from kb import vault as vaultmod
from kb.profile import TEMPLATES
from kb.records import (dump_front_matter, dump_yaml, get_block, link_target_name, links_in_value, parse_excerpts,
                        parse_links, replace_block, split_front_matter, without_section)
from kb.util import KBError, append_log, atomic_write, emit, parse_time

INDEX_SCHEMA = "1"
INDEX_META_KEYS = ("built_at", "schema", "vault_name")   # other meta keys (digests ...) survive rebuilds

DERIVED_SQL = """
CREATE TABLE records(id TEXT PRIMARY KEY, path TEXT, type TEXT, origin TEXT, role TEXT, title TEXT,
                     description TEXT, status TEXT, fm_json TEXT, mtime REAL);
CREATE TABLE props(id TEXT, key TEXT, value TEXT);
CREATE TABLE links(src TEXT, dst TEXT, anchor TEXT, kind TEXT, resolved INT);
CREATE TABLE excerpts(record_id TEXT, eid TEXT, text TEXT, location TEXT, withdrawn INT, gone TEXT);
CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, kind UNINDEXED, ref UNINDEXED, content);
CREATE INDEX props_key ON props(key, value);
CREATE INDEX links_dst ON links(dst);
"""
KEPT_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watch_state(target TEXT, record_id TEXT, state_json TEXT, checked_at TEXT,
                                       PRIMARY KEY(target, record_id));
CREATE TABLE IF NOT EXISTS watch_events(n INTEGER PRIMARY KEY, target TEXT, record_id TEXT, at TEXT,
                                        summary TEXT, detail_json TEXT, alerted INT);
"""
DERIVED_TABLES = ("records", "props", "links", "excerpts", "fts")


# --- shared helpers (also used by lint and query) ---------------------------------

def db_path(vault) -> Path:
    return vault.kb_dir / "kb.sqlite"


def connect(vault, readonly: bool = False) -> sqlite3.Connection:
    p = db_path(vault)
    if readonly:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(p)
        con.executescript(KEPT_SQL)
    con.row_factory = sqlite3.Row
    return con


def text_path(vault, rec) -> Path | None:
    """The record's extracted-text file: front matter `text`, else text/<id>.md for text-bearing types."""
    t = rec.fm.get("text")
    if isinstance(t, str) and t.strip():
        return vault.root / t.strip()
    td = vault.typedef(rec)
    if td and td.text != "none":
        return vault.root / "text" / f"{rec.id}.md"
    return None


def built_at(vault) -> float | None:
    """Epoch seconds of the last index build, or None if there is no usable index."""
    p = vault.root / ".kb" / "kb.sqlite"
    if not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = dict(con.execute("SELECT key, value FROM meta WHERE key IN ('built_at', 'schema')").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        return None
    t = parse_time(rows.get("built_at"))
    return t.timestamp() if t and rows.get("schema") == INDEX_SCHEMA else None


def _source_mtime(vault) -> float:
    """Newest mtime of the files the index is built from (and their folders, so deletions count).
    Files that build itself writes (views/, AGENTS.md) are left out."""
    newest = (vault.root / "kb.yaml").stat().st_mtime
    skip = {".kb", ".git", ".obsidian", "originals", "views", "inbox", "intake", "templates"}
    for dirpath, dirnames, filenames in os.walk(vault.root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if not (d == vault.root and n in skip) and not n.startswith(".")]
        newest = max(newest, d.stat().st_mtime if d != vault.root else 0)
        for fn in filenames:
            if fn.endswith(".md") and not (d == vault.root and fn in vaultmod.EXEMPT_FILES):
                newest = max(newest, (d / fn).stat().st_mtime)
    return newest


def ensure_index(vault) -> None:
    """Rebuild the index when it is missing or any record or text file is newer than meta.built_at."""
    t = built_at(vault)
    if t is None or _source_mtime(vault) > t:
        build_index(vault)


# --- the index ----------------------------------------------------------------------

def _scalar(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str, ensure_ascii=False)
    return str(v)


def _registry_names(vault) -> set:
    names = {vault.name}
    for n, v in vaultmod.load_registry()["vaults"].items():
        if isinstance(v, dict) and v.get("path") and vaultmod.expand(v["path"]) == vault.root:
            names.add(n)
    return names


def repo_docs(vault, warn=None):
    """Yield (repo, rel_path, commit, text) for registered repos whose `vault:` is this vault."""
    names = _registry_names(vault)
    for repo, spec in (vaultmod.load_registry()["repos"] or {}).items():
        if not isinstance(spec, dict) or spec.get("vault") not in names or not spec.get("path"):
            continue
        root = vaultmod.expand(spec["path"])
        try:
            commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                    text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            if warn:
                warn(f"repo {repo}: {root} is not a git repo with commits; skipped (fix the path with `kb registry`)")
            continue
        seen = set()
        for pattern in spec.get("docs") or ["README.md"]:
            for p in sorted(root.glob(pattern)):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    yield repo, p.relative_to(root).as_posix(), commit, p.read_text(encoding="utf-8", errors="replace")


def build_index(vault, warn=None) -> dict:
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    vault.reload()
    con = connect(vault)
    stats = {"records": 0, "links": 0, "excerpts": 0, "texts": 0, "repo_docs": 0}
    try:
        with con:
            for t in DERIVED_TABLES:
                con.execute(f"DROP TABLE IF EXISTS {t}")
            con.executescript(DERIVED_SQL)
            for rec in vault.records:
                _index_record(vault, con, rec, stats)
            for repo, rel, commit, text in repo_docs(vault, warn):
                con.execute("INSERT INTO fts VALUES (?,?,?,?)", (repo, "repo-doc", f"{repo}:{rel}@{commit}", text))
                stats["repo_docs"] += 1
            con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                            [("built_at", started), ("schema", INDEX_SCHEMA), ("vault_name", vault.name)])
    finally:
        con.close()
    return stats


def _index_record(vault, con, rec, stats):
    td = vault.typedef(rec)
    fm = rec.fm
    con.execute("INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", (
        rec.id, rec.rel, rec.type, td.origin if td else None, td.role if td else None, rec.title,
        _scalar(fm.get("description")), _scalar(fm.get("status") or "active"),
        json.dumps(fm, default=str, ensure_ascii=False), rec.path.stat().st_mtime))
    stats["records"] += 1
    for k, v in fm.items():
        for item in (v if isinstance(v, list) else [v]):
            con.execute("INSERT INTO props VALUES (?,?,?)", (rec.id, k, _scalar(item)))
    links = [(l, "embed" if l.embed else "body") for l in parse_links(rec.body)]
    links += [(l, "fm") for l in links_in_value(fm)]
    for l, kind in links:
        target = vault.resolve(l.target)
        dst = target.id if hasattr(target, "id") else (vault.rel(target) if target else l.target)
        con.execute("INSERT INTO links VALUES (?,?,?,?,?)", (rec.id, dst, l.anchor, kind, int(target is not None)))
        stats["links"] += 1
    for e in parse_excerpts(rec.body):
        con.execute("INSERT INTO excerpts VALUES (?,?,?,?,?,?)",
                    (rec.id, e.eid, e.text, e.location, int(bool(e.withdrawn)), e.gone or None))
        con.execute("INSERT INTO fts VALUES (?,?,?,?)", (rec.id, "excerpt", f"{rec.id}#{e.eid}", e.text))
        stats["excerpts"] += 1
    con.execute("INSERT INTO fts VALUES (?,?,?,?)",
                (rec.id, "body", rec.id, f"{rec.title}\n{_scalar(fm.get('description'))}\n{without_section(rec.body, 'Excerpts')}"))
    tp = text_path(vault, rec)
    if tp and tp.is_file():
        con.execute("INSERT INTO fts VALUES (?,?,?,?)",
                    (rec.id, "text", vault.rel(tp), split_front_matter(tp.read_text(encoding="utf-8", errors="replace"))[1]))
        stats["texts"] += 1


# --- views ----------------------------------------------------------------------------

def _is_image_type(td) -> bool:
    return "file" in td.keys


def _contexts(vault) -> list:
    return [str(c["id"] if isinstance(c, dict) else c) for c in vault.profile.get("contexts") or []
            if (c.get("id") if isinstance(c, dict) else c)]


# Columns after the type's required keys: status-like keys, then links, then the rest. Capped at 9.
VIEW_FIRST = ("phase", "severity", "finding_status", "context", "depth", "tool", "owner")
VIEW_LATER = ("sensitive", "captured", "contributor", "issued", "author", "last_checked", "started", "repo")


def _view_keys(td, limit: int = 9) -> list:
    keys = [k for k in td.required if k in td.keys]
    keys += [k for k in VIEW_FIRST if k in td.keys]
    keys += [k for k, spec in td.keys.items() if isinstance(spec, list)]  # enums filter well
    keys += [k for k, spec in td.keys.items() if spec in ("link", "links")]
    keys += [k for k in VIEW_LATER if k in td.keys]
    out = []
    for k in keys:
        if k not in out and k not in ("file", "text"):
            out.append(k)
    return out[:limit]


# The vault's path inside whatever Obsidian vault is open: "" normally, "agent-vault/" when the kb
# vault is nested (e.g. a submodule). Views live in views/, and `this` is the base file when opened.
_ROOT = 'this.file.folder.replace(/views$/, "")'


def _in(folder: str) -> str:
    return f'file.inFolder({_ROOT} + "{folder}")'


def base_view(vault, td) -> dict:
    """An Obsidian Bases definition (https://obsidian.md/help/bases/syntax) for one type."""
    cols = ["file.name", "note.title", "note.description", "note.status"]
    if td.origin == "external" and vault.profile.tiers:
        cols.append("note.tier")
    cols += [f"note.{k}" for k in _view_keys(td) if f"note.{k}" not in cols]
    views = [{"type": "table", "name": td.name, "order": cols}]
    if _is_image_type(td):
        views.append({"type": "cards", "name": f"{td.name} cards", "order": cols[:3], "image": "note.file"})
    if "{context}" in td.folder:
        for ctx in _contexts(vault):
            folder = td.folder_for(context=ctx).split("{", 1)[0].rstrip("/")
            v = {"type": "cards" if _is_image_type(td) else "table", "name": ctx,
                 "filters": {"and": [_in(folder)]}, "order": cols}
            if _is_image_type(td):
                v["image"] = "note.file"
            views.append(v)
    return {"filters": {"and": [f'note.type == "{td.name}"', f"file.path.startsWith({_ROOT})",
                                {"not": [_in("templates"), _in("views")]},
                                'note.status != "superseded"']},
            "views": views}


def _cell(v) -> str:
    return _scalar(v).replace("|", "\\|").replace("\n", " ")


def write_views(vault) -> list:
    out = []
    for td in vault.profile.types.values():
        for name, text in ((f"{td.name}.base", dump_yaml(base_view(vault, td))),):
            p = vault.root / "views" / name
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                atomic_write(p, text)
            out.append(f"views/{name}")
    for p in (vault.root / "views").glob("*.md"):  # static catalogues from older kb versions
        fm, _ = split_front_matter(p.read_text(encoding="utf-8", errors="replace"))
        if fm and fm.get("type") == "generated":
            p.unlink()
    return out


# --- AGENTS.md block --------------------------------------------------------------------

_DEFAULT_TRAITS = {"original": "none", "text": "none", "quotes": "none", "tracked": "none", "claims": "free"}


def agents_block(vault) -> str:
    rows = ["| type | folder | records | traits |", "|---|---|---|---|"]
    for td in vault.profile.types.values():
        traits = [f"origin: {td.origin}", f"role: {td.role}"]
        traits += [f"{t}: {getattr(td, t)}" for t, d in _DEFAULT_TRAITS.items() if getattr(td, t) != d]
        rows.append(f"| {td.name} | {td.folder}/ | {len(vault.of_type(td.name))} | {', '.join(traits)} |")
    tmpl = (TEMPLATES / "agents-block.md").read_text(encoding="utf-8")
    desc = str(vault.profile.get("description") or "").strip()
    return tmpl.replace("{name}", vault.name).replace("{description}", desc).replace("{routing}", "\n".join(rows)).strip()


def write_agents(vault) -> bool:
    p = vault.root / "AGENTS.md"
    old = p.read_text(encoding="utf-8") if p.exists() else f"# {vault.name}: agent instructions\n"
    new = replace_block(old, "agents", agents_block(vault))
    if new != old:
        atomic_write(p, new)
    return new != old


# --- command ------------------------------------------------------------------------------

# --- generated blocks in records -------------------------------------------------------------

def evidence_table(vault, rec) -> str | None:
    """Rows for every record whose `supports` links to rec (spec: outputs show their evidence)."""
    rows = []
    for r in vault.records:
        targets = {link_target_name(l.target).casefold() for l in links_in_value(r.fm.get("supports"))}
        if rec.id.casefold() in targets:
            who = ", ".join(map(str, r.fm.get("contributor") or r.fm.get("author") or [])) if isinstance(
                r.fm.get("contributor") or r.fm.get("author"), list) else _scalar(r.fm.get("contributor") or "")
            rows.append(f"| [[{r.id}]] | {_cell(r.fm.get('phase') or '')} | {_cell(r.fm.get('description') or r.title)} "
                        f"| {_cell(who)} |")
    if not rows:
        return None
    return "\n".join(["## Evidence (generated)", "", "| evidence | phase | shows | by |", "|---|---|---|---|", *sorted(rows)])


def write_record_blocks(vault) -> int:
    changed = 0
    for rec in vault.records:
        td = vault.typedef(rec)
        if not td or td.role != "output":
            continue
        table = evidence_table(vault, rec)
        if table is None and get_block(rec.body, "evidence") is None:
            continue
        body = replace_block(rec.body, "evidence", table or "No evidence links to this record yet.")
        if body != rec.body:
            rec.body = body
            rec.save()
            changed += 1
    return changed


# --- README (the vault's purpose doc) navigation block ------------------------------------------

def nav_block(vault) -> str:
    lines = ["## Contents (generated)", ""]
    for td in vault.profile.types.values():
        n = len(vault.of_type(td.name))
        lines.append(f"- **{td.name}** ({n}): [[{td.name}.base]]")
    for role, label in (("synthesis", "Notes"), ("output", "Outputs")):
        recs = sorted((r for r in vault.records if (vault.typedef(r) and vault.typedef(r).role == role
                       and vault.typedef(r).origin == "internal" and vault.typedef(r).tracked != "watch")),
                      key=lambda r: r.id)
        if recs:
            lines += ["", f"**{label}:**"] + [f"- [[{r.id}]] — {_scalar(r.fm.get('description') or r.title)}" for r in recs]
    return "\n".join(lines)


def write_readme_nav(vault, warn=None) -> bool:
    p = vault.root / "README.md"
    if not p.exists():
        if warn:
            warn("README.md is missing: write the vault's purpose doc (`kb init` creates a template)")
        return False
    old = p.read_text(encoding="utf-8")
    if warn and "<!-- kb:todo -->" in old:
        warn("README.md still has <!-- kb:todo --> placeholders: fill in the purpose, scope and consumers")
    new = replace_block(old, "nav", nav_block(vault))
    if new != old:
        atomic_write(p, new)
    return new != old


# --- Obsidian property types ------------------------------------------------------------------

_CORE_TYPES = {"tags": "tags", "aliases": "aliases", "updated": "date", "accessed": "date", "last_checked": "date",
               "mined_on": "date", "contributor": "multitext", "author": "multitext", "supports": "multitext",
               "entities": "multitext", "watch": "multitext", "sha256": "text", "captured": "text"}
_SPEC_TYPES = {"list": "multitext", "links": "multitext", "link": "text", "bool": "checkbox", "int": "number",
               "string": "text"}


def write_obsidian_types(vault) -> bool:
    """Declare property types in .obsidian/types.json so Properties shows dates, lists and checkboxes
    consistently. Dates from the profile stay text when partial dates (2020-09) are allowed."""
    p = vault.root / ".obsidian" / "types.json"
    if not p.parent.is_dir():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        data = {}
    types = data.setdefault("types", {})
    wanted = dict(_CORE_TYPES)
    for td in vault.profile.types.values():
        for k, spec in td.keys.items():
            if isinstance(spec, list):
                wanted.setdefault(k, "text")
            elif spec in _SPEC_TYPES:
                wanted.setdefault(k, _SPEC_TYPES[spec])
            elif spec == "date":
                wanted.setdefault(k, "text")  # may hold 2020-09 or 2020
    changed = False
    for k, t in wanted.items():
        if k not in types:  # never override a type the user set in Obsidian
            types[k], changed = t, True
    if changed:
        atomic_write(p, json.dumps(data, indent=2) + "\n")
    return changed


def build(vault, warn=None) -> dict:
    stats = {"blocks": write_record_blocks(vault)}
    if stats["blocks"]:
        vault.reload()
    stats.update(build_index(vault, warn))
    stats["views"] = write_views(vault)
    stats["agents_updated"] = write_agents(vault)
    stats["readme_updated"] = write_readme_nav(vault, warn)
    stats["obsidian_types"] = write_obsidian_types(vault)
    return stats


def run(args) -> int:
    v = vaultmod.open_from_args(args)
    warnings = []
    stats = build(v, warnings.append)
    stats["warnings"] = warnings
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    detail = f"{stats['records']} records, {stats['excerpts']} excerpts, {stats['repo_docs']} repo docs"
    emit(args, stats, f"built {db_path(v).relative_to(v.root)}: {detail}; {len(stats['views'])} view files")
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("build", parents=[common], help="rebuild the SQLite index, views and AGENTS.md block")
    p.set_defaults(func=run)
