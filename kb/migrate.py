"""kb migrate originals: convert records from the nested `originals:` list to flat Properties keys
(`original`, `sha256`, `captured`) plus a capture.yaml manifest per capture folder, and repo clone
paths to the portable `clone:<host>/<owner>/<repo>@<commit>` form. Idempotent."""

import datetime as dt
import re
from pathlib import Path

from kb import capture, vault as vaultmod
from kb.util import KBError, append_log, emit, sha256_file

_AUX = ("headers.txt", "tree.txt", "commit.txt")


def _main_entry(entries):
    """The main file of one capture folder: commit.txt for repos, else the first non-aux entry
    (kb capture listed the main file first)."""
    names = [Path(str(o["path"])).name for o in entries]
    if "commit.txt" in names:
        return entries[names.index("commit.txt")]
    for o, n in zip(entries, names):
        if n not in _AUX and not n.startswith("README"):
            return o
    return entries[0]


def _check_folder(vault, folder: Path, entries) -> list:
    """Problems that stop a folder's migration: listed files missing or changed, files nobody listed."""
    bad, listed = [], set()
    for o in entries:
        p = vault.root / str(o["path"])
        listed.add(p.name)
        if not p.is_file():
            bad.append(f"{o['path']} (missing)")
        elif o.get("sha256") and sha256_file(p) != o["sha256"]:
            bad.append(f"{o['path']} (sha256 differs)")
    capture_folder = vault.rel(folder).startswith("originals/")  # evidence sits beside other files
    for f in sorted(folder.iterdir()) if capture_folder and folder.is_dir() else ():
        if f.is_file() and f.name != capture.MANIFEST and f.name not in listed:
            bad.append(f"{vault.rel(f)} (not listed)")
    return bad


def _portable_clone(rec):
    """(new text, old path, new path) for an old `clone:<machine path>@<sha>`, or None."""
    c = capture.parse_clone(rec.fm.get("text"))
    if c is None or c[2]:
        return None
    old_path, commit, _ = c
    url = rec.fm.get("url") or rec.fm.get("given_url")
    if not url:
        raise KBError(f"{rec.rel}: can't tell host/owner/repo for {rec.fm['text']!r} without `url:`; add the repo url")
    host, owner, repo = capture._repo_parts(str(url))
    ref = f"{host}/{owner}/{repo}"
    return f"clone:{ref}@{commit}", old_path, capture.clones_root() / ref


def migrate_record(vault, rec, dry_run=False) -> dict:
    """Convert one record. Returns {id, folders, refused: [..], clone: (old, new) | None, changed}."""
    fm = rec.fm
    td = vault.typedef(rec)
    res = {"id": rec.id, "folders": 0, "refused": [], "clone": None, "changed": False}
    entries = [o for o in fm.get("originals") or [] if isinstance(o, dict) and o.get("path")]
    groups = {}
    for o in entries:
        groups.setdefault(str(Path(str(o["path"])).parent), []).append(o)
    newest = None
    for folder_rel, es in groups.items():
        folder = vault.root / folder_rel
        bad = _check_folder(vault, folder, es)
        if bad:
            res["refused"] += bad
            continue
        main = _main_entry(es)
        key = (str(main.get("captured") or ""), capture._natural(folder.name))
        if newest is None or key > newest[0]:
            newest = (key, main)
        if folder_rel.startswith("originals/"):
            files = [{"name": Path(str(o["path"])).name, "sha256": sha256_file(vault.root / str(o["path"])),
                      "bytes": (vault.root / str(o["path"])).stat().st_size,
                      "format": o.get("format") or capture._format(Path(str(o["path"])).name)} for o in es]
            info = {**main, "main": Path(str(main["path"])).name, "given_url": None}
            if not dry_run:
                capture.write_manifest(folder, info, files)
            res["folders"] += 1
    if res["refused"]:
        return res  # nothing changes on the card until every folder checks out
    out = {}
    for k, val in fm.items():
        if k == "originals":
            if newest and td is not None and td.original == "immutable":
                main = newest[1]
                out.update(original=str(main["path"]), sha256=main.get("sha256") or
                           sha256_file(vault.root / str(main["path"])))
                if not str(main["path"]).startswith("originals/"):  # evidence: the file sits next to the sidecar
                    out.update(bytes=main.get("bytes"), format=main.get("format"))
                out["captured"] = fm.get("captured") or main.get("captured")
            continue
        if k == "originals_note":
            out["original_note"] = val
            continue
        if k == "captured" and "captured" in out:
            continue
        if k == "updated" and isinstance(val, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", val):
            val = dt.date.fromisoformat(val)  # older triage quoted it; Properties wants a date
        out[k] = val
    out = {k: v for k, v in out.items() if v is not None}
    clone = _portable_clone(rec)
    if clone:
        out["text"] = clone[0]
        res["clone"] = (str(clone[1]), str(clone[2]))
    res["changed"] = out != fm or list(out) != list(fm)
    if res["changed"] and not dry_run:
        rec.fm = out
        rec.save()
    return res


def _needs(rec) -> bool:
    c = capture.parse_clone(rec.fm.get("text"))
    return "originals" in rec.fm or "originals_note" in rec.fm or (c is not None and not c[2])


def migrate_originals(vault, dry_run=False) -> dict:
    results = [migrate_record(vault, r, dry_run) for r in vault.records if _needs(r)]
    changed = [r for r in results if r["changed"]]
    rep = {"dry_run": dry_run, "records": len(changed), "folders": sum(r["folders"] for r in results),
           "refused": {r["id"]: r["refused"] for r in results if r["refused"]},
           "clones": [r["clone"] for r in results if r["clone"]]}
    if changed and not dry_run:
        append_log(vault.root, "migrate", "originals", f"{rep['records']} records, {rep['folders']} capture folders")
    return rep


def _describe(rep) -> list:
    verb = "would migrate" if rep["dry_run"] else "migrated"
    lines = [f"{verb} {rep['records']} records, {rep['folders']} capture folders"]
    for rid, bad in rep["refused"].items():
        lines.append(f"refused {rid}: files don't match its originals list; restore them from git and re-run:")
        lines += [f"  {b}" for b in bad]
    moves = [(o, n) for o, n in rep["clones"] if Path(o).exists() and not Path(n).exists()]
    if moves:
        lines.append("clones to move into the standard location (or re-clone with `kb capture <url>`):")
        lines += [f"  mkdir -p {Path(n).parent} && mv {o} {n}" for o, n in moves]
    return lines


def cmd_originals(args) -> int:
    v = vaultmod.open_from_args(args)
    rep = migrate_originals(v, dry_run=args.dry_run)
    emit(args, rep, _describe(rep))
    return 1 if rep["refused"] else 0


def register(subparsers, common):
    p = subparsers.add_parser("migrate", parents=[common], help="upgrade a vault written by an older kb")
    sub = p.add_subparsers(dest="migrate_cmd", metavar="SUBCOMMAND", required=True)
    o = sub.add_parser("originals", parents=[common],
                       help="nested originals: -> flat original/sha256/captured + capture.yaml; portable clone paths")
    o.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    o.set_defaults(func=cmd_originals)
