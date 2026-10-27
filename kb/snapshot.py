"""kb snapshot: annotated git tags kb/<name> marking what a vault knew at a moment (spec §1.6)."""

import os
import re
import sys

from kb.profile import load_profile
from kb.util import KBError, append_log, emit, iso, now_utc
from kb.vault import expand, load_registry, open_from_args
from kb.watch import _git


def toplevel(root):
    p = _git("rev-parse", "--show-toplevel", cwd=root, check=False)
    if p.returncode:
        raise KBError(f"{root} is not inside a git repo; run `git init` there (and commit) before snapshotting")
    return p.stdout.strip()


def create(root, name, message):
    """Tag HEAD of the repo holding `root` as kb/<name>; returns {repo, tag, sha, dirty}."""
    top, tag = toplevel(root), f"kb/{name}"
    if _git("rev-parse", "-q", "--verify", f"refs/tags/{tag}", cwd=top, check=False).returncode == 0:
        raise KBError(f"tag {tag} already exists in {top}; pick another name (tags are never moved)")
    sha = _git("rev-parse", "HEAD", cwd=top, check=False).stdout.strip()
    if not sha or sha == "HEAD":
        raise KBError(f"{top} has no commits yet; commit the vault first")
    dirty = bool(_git("status", "--porcelain", "--", str(root), cwd=top).stdout.strip())
    _git("tag", "-a", tag, "-m", message, cwd=top)
    append_log(root, "snapshot", tag, sha[:7] + (" (uncommitted changes not included)" if dirty else ""))
    return {"repo": top, "tag": tag, "sha": sha, "dirty": dirty}


def cmd_snapshot(args):
    if args.name == "list":
        return cmd_list(args)
    if not args.name:
        raise KBError("give a snapshot NAME (e.g. kb snapshot 2026-event-morning) or `kb snapshot list`")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.name) or ".." in args.name or args.name.endswith(".lock"):
        raise KBError(f"bad snapshot name {args.name!r}: use letters, digits, '.', '_' and '-'")
    msg = args.message or f"kb snapshot {args.name} at {iso(now_utc())}"
    done, skipped, failed = [], [], []
    if args.all:
        seen = set()
        for vname, entry in load_registry()["vaults"].items():
            root = expand(entry.get("path", ""))
            try:
                access = load_profile(root).access
            except KBError as e:
                skipped.append({"vault": vname, "reason": str(e)})
                continue
            if access == "local" and os.environ.get("KB_AGENT") != "local":
                skipped.append({"vault": vname, "reason": "local-only vault (needs KB_AGENT=local)"})
                continue
            try:
                top = toplevel(root)
                if top in seen:  # several vaults in one repo share a tag
                    skipped.append({"vault": vname, "reason": f"same repo as an earlier vault ({top})"})
                    continue
                seen.add(top)
                done.append({"vault": vname, **create(root, args.name, msg)})
            except KBError as e:
                failed.append({"vault": vname, "error": str(e)})
    else:
        vault = open_from_args(args)
        done.append({"vault": vault.name, **create(vault.root, args.name, msg)})

    def text(d):
        lines = [f"{r['vault']}: {r['tag']} at {r['sha'][:7]} in {r['repo']}"
                 + (" (warning: uncommitted changes are not in the snapshot)" if r["dirty"] else "") for r in d["done"]]
        lines += [f"{s['vault']}: skipped, {s['reason']}" for s in d["skipped"]]
        for f in d["failed"]:
            print(f"kb snapshot: {f['vault']}: {f['error']}", file=sys.stderr)
        return lines + ["(not pushed; push tags yourself: git push origin --tags)"] * bool(d["done"])
    emit(args, {"done": done, "skipped": skipped, "failed": failed}, text)
    return 1 if failed else 0


def cmd_list(args):
    vault = open_from_args(args)
    out = _git("tag", "-l", "kb/*", "--format=%(refname:short)\t%(creatordate:short)\t%(subject)",
               cwd=toplevel(vault.root)).stdout
    rows = [dict(zip(("tag", "date", "message"), line.split("\t", 2))) for line in out.splitlines() if line]
    emit(args, rows, lambda rs: [f"{r['tag']} · {r['date']} · {r['message']}" for r in rs] or ["no kb/* snapshots"])
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("snapshot", parents=[common], help="tag the vault's repo as kb/NAME (or `snapshot list`)")
    p.add_argument("name", nargs="?", metavar="NAME", help="snapshot name, or `list`")
    p.add_argument("--all", action="store_true", help="every registry vault this agent may access")
    p.add_argument("-m", "--message", metavar="MSG")
    p.set_defaults(func=cmd_snapshot)
