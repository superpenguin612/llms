"""kb watch: poll repos, GitHub users/releases, pages and feeds on a cadence (spec §1.5, §2.8)."""

import fcntl
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from kb import capture, notify as notify_mod
from kb.records import append_to_section
from kb.util import KBError, append_log, emit, iso, is_due, now_utc, parse_time, sha256_bytes, sha256_file, today
from kb.vault import open_from_args

KINDS = ("git", "github-user", "github-releases", "page", "feed")
UA = "kb-watch (+https://github.com/superpenguin612/llms)"


# --- helpers -----------------------------------------------------------------------

def _git(*args, cwd=None, timeout=120, check=True) -> subprocess.CompletedProcess:
    cmd = ["git"] + (["-C", str(cwd)] if cwd else []) + list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise KBError("git is not installed; install it (e.g. apt install git)") from None
    except subprocess.TimeoutExpired:
        raise KBError(f"`git {args[0]}` timed out after {timeout}s") from None
    if check and p.returncode:
        raise KBError(f"`git {' '.join(args[:2])}` failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p


def _http(url, headers=None, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _gh(vault, path):
    env = ((vault.profile.get("collectors") or {}).get("github") or {}).get("token_env") or "GITHUB_TOKEN"
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get(env):
        headers["Authorization"] = f"Bearer {os.environ[env]}"
    try:
        return json.loads(_http("https://api.github.com" + path, headers))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            reset = e.headers.get("X-RateLimit-Reset", "") if e.headers else ""
            raise KBError(f"GitHub API {e.code} (rate limited?) for {path}; set {env} or wait"
                          + (f" until {reset}" if reset else "")) from None
        raise KBError(f"GitHub API {path}: HTTP {e.code}") from None


def mirror_path(url: str) -> Path:
    """<clones root>/<host>/<owner>/<repo>.git for https, ssh and scp-style URLs."""
    host, owner, repo = capture._repo_parts(url)
    return capture.clones_root() / host / owner / f"{repo}.git"


# explicit refspecs, so --prune only touches what the remote has and never kb's refs/kb/* pins
MIRROR_REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*", "+refs/pull/*:refs/pull/*")


def fetch_mirror(vault, url) -> Path:
    """Clone or fetch a bare mirror under the clones root (see kb.capture.clones_root)."""
    path = mirror_path(url)
    if (path / "HEAD").exists():
        _git("fetch", "--prune", "--quiet", "origin", *MIRROR_REFSPECS, cwd=path, timeout=600)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--mirror", "--quiet", url, str(path), timeout=1200)
    return path


def _short(ref: str) -> str:
    for p, label in (("refs/heads/", ""), ("refs/tags/", "tag ")):
        if ref.startswith(p):
            return label + ref[len(p):]
    return ref


def _pin(mirror, sha) -> str:
    """Keep a commit the remote dropped (force-push, deleted branch) as refs/kb/<sha>; a summary suffix."""
    ok = _git("update-ref", f"refs/kb/{sha}", sha, cwd=mirror, check=False).returncode == 0
    return "; old head pinned" if ok else ""


# --- checkers: (vault, value, old_state) -> (state, [(summary, detail)]) --------------

def check_git(vault, url, old):
    out = _git("ls-remote", "--heads", "--tags", url, timeout=60).stdout
    state = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, ref = line.split("\t", 1)
        if ref.endswith("^{}"):
            state[ref[:-3]] = sha  # annotated tag: track the commit, not the tag object
        else:
            state.setdefault(ref, sha)
    mirror = fetch_mirror(vault, url)
    if old is None:
        return state, []
    changes = []
    for ref in sorted(set(old) | set(state)):
        a, b, name = old.get(ref), state.get(ref), _short(ref)
        detail = {"ref": ref, "old": a, "new": b}
        if a is None:
            changes.append((f"new {'branch ' * ref.startswith('refs/heads/')}{name} ({b[:7]})", detail))
        elif b is None:
            changes.append((f"{name} deleted (was {a[:7]}{_pin(mirror, a)})", detail))
        elif a != b:
            summary = f"{name}: {a[:7]} → {b[:7]}"
            if mirror:
                anc = _git("merge-base", "--is-ancestor", a, b, cwd=mirror, check=False).returncode
                if anc == 1:
                    detail["forced"] = True
                    summary += f" (force-push{_pin(mirror, a)})"
                elif anc == 0:
                    subjects = _git("log", "--format=%s", f"{a}..{b}", cwd=mirror, check=False).stdout.splitlines()
                    detail.update(commits=len(subjects), subjects=subjects[:20])
                    summary += f" ({len(subjects)} commit{'s' * (len(subjects) != 1)}: " + "; ".join(subjects[:3]) \
                               + ("; …" if len(subjects) > 3 else "") + ")"
            changes.append((summary, detail))
    return state, changes


def check_github_user(vault, login, old):
    # shortcut: first 100 repos by creation date only; paginate if a watched account has more
    repos = _gh(vault, f"/users/{login}/repos?per_page=100&sort=created")
    state = {r["full_name"]: r.get("pushed_at") for r in repos}
    if old is None:
        return state, []
    changes = []
    for name, pushed in state.items():
        if name not in old:
            changes.append((f"new repo {name}", {"repo": name, "pushed_at": pushed}))
        elif pushed != old[name]:
            changes.append((f"{name} pushed ({pushed})", {"repo": name, "old": old[name], "pushed_at": pushed}))
    changes += [(f"repo {n} gone (deleted, renamed or private)", {"repo": n}) for n in old if n not in state]
    return state, changes


def check_github_releases(vault, repo, old):
    rels = _gh(vault, f"/repos/{repo}/releases?per_page=100")
    state = {r["tag_name"]: r.get("published_at") for r in rels}
    if old is None:
        return state, []
    return state, [(f"new release {t}" + (f" ({r['name']})" if r.get("name") and r["name"] != t else ""),
                    {"tag": t, "url": r.get("html_url"), "published_at": r.get("published_at")})
                   for r in rels if (t := r["tag_name"]) not in old]


def _page_text(data: bytes, url: str) -> str:
    try:
        from kb.extract import extract  # optional; written separately
        out = extract(data, url=url)
        text = getattr(out, "text", out)
        if isinstance(text, str) and text.strip():
            return text
    except Exception:
        pass
    t = data.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", t)
    t = re.sub(r"(?s)<!--.*?-->|<[^>]+>", " ", t)
    return html.unescape(t)


def check_page(vault, url, old):
    from kb.util import normalize
    digest = sha256_bytes(normalize(_page_text(_http(url), url)).encode("utf-8"))
    state = {"sha256": digest}
    if old is None or old.get("sha256") == digest:
        return state, []
    return state, [("page changed", {"url": url, "old": old.get("sha256"), "new": digest})]


_ATOM = "{http://www.w3.org/2005/Atom}"


def check_feed(vault, url, old):
    try:
        root = ET.fromstring(_http(url))
    except ET.ParseError as e:
        raise KBError(f"feed {url} is not valid XML: {e}") from None
    items = {}
    for it in root.iter("item"):
        key = (it.findtext("guid") or it.findtext("link") or it.findtext("title") or "").strip()
        items[key] = (it.findtext("title") or key).strip()
    for it in root.iter(_ATOM + "entry"):
        link = it.find(_ATOM + "link")
        key = (it.findtext(_ATOM + "id") or (link.get("href") if link is not None else "") or "").strip()
        items[key] = (it.findtext(_ATOM + "title") or key).strip()
    items.pop("", None)
    if old is None:
        return items, []
    # items that roll off the feed are not reported; keep old ids so they don't come back as new
    return {**old, **items}, [(f"new item: {title}", {"id": k, "title": title}) for k, title in items.items() if k not in old]


CHECKERS = {"git": check_git, "github-user": check_github_user, "github-releases": check_github_releases,
            "page": check_page, "feed": check_feed}


def parse_target(target: str):
    kind, sep, value = str(target).partition(":")
    if not sep or kind not in KINDS or not value:
        raise KBError(f"bad watch target {target!r}: use one of " + ", ".join(f"{k}:<...>" for k in KINDS))
    return kind, value


# --- records ------------------------------------------------------------------------

def watched(vault):
    return [r for r in vault.records if (td := vault.typedef(r)) and td.tracked == "watch" and r.fm.get("watch")]


def cadence(vault, rec):
    return rec.fm.get("watch_every") or vault.typedef(rec).watch_every


# --- commands -----------------------------------------------------------------------

def run(vault, ids=(), all_=False, alert=True, now=None):
    """Check targets; returns {"records": [...], "alerts": [...]} or None when another run holds the lock."""
    lock = open(vault.kb_dir / "watch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    try:
        now = now or now_utc()
        if ids:
            recs = [vault.require(i) for i in ids]
            bad = [r.id for r in recs if r not in watched(vault)]
            if bad:
                raise KBError(f"{', '.join(bad)}: not a watched record (needs a tracked: watch type and a watch: list); "
                              f"add a target with `kb watch add ID TARGET`")
        conn = notify_mod.connect(vault)
        if not ids:
            recs = [r for r in watched(vault) if all_ or is_due(_last_checked(conn, r), cadence(vault, r), now)]
        out = []
        try:
            for rec in recs:
                out.append(_run_record(vault, conn, rec, now))
        finally:
            conn.close()
        alerts = notify_mod.notify(vault) if alert else []
        return {"records": out, "alerts": alerts}
    finally:
        lock.close()


def _last_checked(conn, rec):
    """Oldest successful check across the record's targets (None if any target was never checked).
    Kept in the index, not front matter, so quiet runs don't touch files (no no-op commits)."""
    targets = rec.fm.get("watch") or []
    rows = conn.execute(f"SELECT checked_at FROM watch_state WHERE record_id=? AND target IN ({','.join('?' * len(targets))})",
                        (rec.id, *targets)).fetchall() if targets else []
    if len(rows) < len(targets):
        return None
    return min(r[0] for r in rows) if rows else rec.fm.get("last_checked")


def _run_record(vault, conn, rec, now):
    stamp = now.strftime("%Y-%m-%d %H:%MZ")
    res = {"id": rec.id, "changes": [], "errors": [], "baseline": []}
    for target in rec.fm.get("watch") or []:
        try:
            kind, value = parse_target(target)
            row = conn.execute("SELECT state_json FROM watch_state WHERE target=? AND record_id=?",
                               (target, rec.id)).fetchone()
            old = json.loads(row[0]) if row else None
            state, changes = CHECKERS[kind](vault, value, old)
        except Exception as e:  # one bad target (rate limit, network, parse) must not stop the run
            res["errors"].append({"target": target, "error": str(e) or type(e).__name__})
            continue
        if old is None:
            res["baseline"].append(target)
        for summary, detail in changes:
            conn.execute("INSERT INTO watch_events(target, record_id, at, summary, detail_json, alerted) "
                         "VALUES(?,?,?,?,?,0)", (target, rec.id, iso(now), summary, json.dumps(detail)))
            res["changes"].append({"target": target, "summary": summary, "detail": detail})
            append_log(vault.root, "watch", rec.id, summary)
        conn.execute("INSERT OR REPLACE INTO watch_state(target, record_id, state_json, checked_at) VALUES(?,?,?,?)",
                     (target, rec.id, json.dumps(state, sort_keys=True), iso(now)))
    for c in reversed(res["changes"]):  # top-inserted, so reversed keeps this run's order
        rec.body = append_to_section(rec.body, "Timeline", f"- {stamp} — {c['summary']}", top=True)
    if res["changes"]:  # quiet runs leave the file alone; failed targets stay due (their checked_at isn't updated)
        rec.fm["last_checked"] = iso(now)
        rec.save()
        res["path"] = str(rec.path)
    conn.commit()
    return res


def commit_push(vault, records, push: bool) -> dict:
    """Commit only the record files this run changed; optionally push. Never force-pushes: a rejected push
    is retried once after `git pull --rebase`; on conflict the rebase is aborted and the commit stays local."""
    paths = [r["path"] for r in records if r.get("path")]
    if not paths:
        return {"committed": False, "pushed": False}
    n = sum(len(r["changes"]) for r in records)
    msg = f"kb watch: {n} change(s) in {', '.join(sorted(r['id'] for r in records if r.get('path')))}"
    root = str(vault.root)
    _git("add", "--", *paths, cwd=root)
    _git("commit", "-q", "-m", msg, "--", *paths, cwd=root)
    out = {"committed": True, "message": msg, "pushed": False}
    if push:
        if _git("push", "-q", cwd=root, check=False).returncode != 0:
            if _git("pull", "-q", "--rebase", "--autostash", cwd=root, check=False).returncode != 0:
                _git("rebase", "--abort", cwd=root, check=False)
                out["error"] = "push rejected and rebase conflicted; commit kept locally, resolve and push by hand"
                return out
            if _git("push", "-q", cwd=root, check=False).returncode != 0:
                out["error"] = "push failed after rebase; commit kept locally"
                return out
        out["pushed"] = True
    return out


def cmd_run(args):
    vault = open_from_args(args)
    res = run(vault, args.ids, all_=args.all, alert=not args.no_alert)
    if res is None:
        print("kb watch: another `kb watch run` holds .kb/watch.lock; skipping this run")
        return 0
    if args.commit or args.push:
        res["git"] = commit_push(vault, res["records"], push=args.push)
        if res["git"].get("error"):
            print(f"kb watch: {res['git']['error']}", file=sys.stderr)

    def text(d):
        lines = []
        for r in d["records"]:
            lines.append(f"{r['id']}: {len(r['changes'])} change(s)"
                         + (f", baseline for {len(r['baseline'])} target(s)" if r["baseline"] else ""))
            lines += [f"  {c['summary']}" for c in r["changes"]]
            for e in r["errors"]:
                print(f"kb watch: {r['id']}: {e['target']}: {e['error']}", file=sys.stderr)
        lines += [f"alert via {a['channel']}: " + ("ok" if a["ok"] else f"failed ({a['error']})") for a in d["alerts"]]
        g = d.get("git") or {}
        if g.get("committed"):
            lines.append(f"committed: {g['message']}" + (" (pushed)" if g.get("pushed") else ""))
        return lines or ["nothing due"]
    emit(args, res, text)
    return 1 if (res.get("git") or {}).get("error") else 0


def cmd_list(args):
    vault = open_from_args(args)
    now = now_utc()
    conn = notify_mod.connect(vault)
    try:
        last = {r.id: _last_checked(conn, r) for r in watched(vault)}
    finally:
        conn.close()
    rows = [{"id": r.id, "watch": r.fm.get("watch"), "every": cadence(vault, r), "last_checked": str(last[r.id] or ""),
             "due": is_due(last[r.id], cadence(vault, r), now)} for r in watched(vault)]

    def text(rows):
        lines = [f"{r['id']} · every {r['every']} · last {r['last_checked'] or 'never'}{' · due' * r['due']}\n  "
                 + "\n  ".join(r["watch"]) for r in rows] or ["no watched records (add one with `kb watch add ID TARGET`)"]
        return lines + ["", f"schedule: cd {vault.root} && kb watch run --due --push (see contrib/systemd in the llms repo)"]
    emit(args, rows, text)
    return 0


def cmd_events(args):
    vault = open_from_args(args)
    conn = notify_mod.connect(vault)
    try:
        q, params = "SELECT * FROM watch_events", []
        if args.since:
            since = parse_time(args.since)
            if since is None:
                raise KBError(f"bad --since {args.since!r}; use YYYY-MM-DD or an ISO time")
            q, params = q + " WHERE at >= ?", [iso(since)]
        rows = [dict(r) for r in conn.execute(q + " ORDER BY n DESC LIMIT ?", params + [args.limit])]
    finally:
        conn.close()
    emit(args, rows, lambda rs: [f"{r['at']} [{r['record_id']}] {r['summary']}" + ("" if r["alerted"] else " (unalerted)")
                                 for r in rs] or ["no events"])
    return 0


def cmd_add(args):
    vault = open_from_args(args)
    parse_target(args.target)
    rec = vault.require(args.id)
    td = vault.typedef(rec)
    if not td or td.tracked != "watch":
        raise KBError(f"{rec.id} is type {rec.type!r}, which isn't tracked: watch; watch an entity record instead")
    targets = list(rec.fm.get("watch") or [])
    if args.target in targets:
        emit(args, {"id": rec.id, "added": False}, f"{rec.id} already watches {args.target}")
        return 0
    rec.fm["watch"] = targets + [args.target]
    rec.save()
    append_log(vault.root, "watch-add", rec.id, args.target)
    emit(args, {"id": rec.id, "added": True}, f"{rec.id}: watching {args.target} (first run records a baseline)")
    return 0


def snapshot(vault, rid):
    rec = vault.require(rid)
    urls = [v for k, v in map(parse_target, rec.fm.get("watch") or []) if k == "git"]
    if not urls:
        raise KBError(f"{rec.id} has no git: watch target; add one with `kb watch add {rec.id} git:<url>`")
    url = urls[0]
    mirror = fetch_mirror(vault, url)
    sha = _git("rev-parse", "HEAD^{commit}", cwd=mirror).stdout.strip()
    name = f"repo-{sha[:7]}.tar.gz"
    for rid_, d in capture.capture_dirs(vault):
        if rid_ == rec.id and (d / name).is_file():
            return {"id": rec.id, "path": vault.rel(d / name), "sha": sha, "new": False}
    folder = capture._date_dir(vault.root / "originals" / rec.id)
    folder.mkdir(parents=True)
    dest = folder / name
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        _git("archive", "--format=tar.gz", "-o", str(tmp), sha, cwd=mirror, timeout=600)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        if not any(folder.iterdir()):
            folder.rmdir()
        raise
    captured = iso(now_utc())
    capture.write_manifest(folder, {"captured": captured, "via": "git archive", "fetched_url": url,
                                    "obtained_from": f"{url}@{sha}", "main": name})
    td = vault.typedef(rec)
    rel = vault.rel(dest)
    if td is not None and td.original == "immutable":
        capture.set_flat(rec.fm, rel, sha256_file(dest), captured)
        rec.save()
    append_log(vault.root, "watch-snapshot", rec.id, f"{rel} ({sha[:7]})")
    return {"id": rec.id, "path": rel, "sha": sha, "new": True}


def cmd_snapshot(args):
    vault = open_from_args(args)
    res = snapshot(vault, args.id)
    emit(args, res, f"{res['id']}: {'saved' if res['new'] else 'already have'} {res['path']} ({res['sha'][:7]})")
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("watch", parents=[common], help="poll watched repos, users, pages and feeds")
    sub = p.add_subparsers(dest="watch_cmd", metavar="SUBCOMMAND", required=True)
    r = sub.add_parser("run", parents=[common], help="check due (or given) watch targets and alert")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--due", action="store_true", help="only records past their watch_every (default)")
    g.add_argument("--all", action="store_true", help="every watched record")
    r.add_argument("--no-alert", action="store_true", help="record events but send no alerts")
    r.add_argument("--commit", action="store_true", help="git-commit the record files this run changed")
    r.add_argument("--push", action="store_true", help="--commit, then push (rebases once if the remote moved)")
    r.add_argument("ids", nargs="*", metavar="ID")
    r.set_defaults(func=cmd_run)
    sub.add_parser("list", parents=[common], help="watched records, cadence, due").set_defaults(func=cmd_list)
    e = sub.add_parser("events", parents=[common], help="recent watch events")
    e.add_argument("--since", metavar="DATE")
    e.add_argument("--limit", type=int, default=50)
    e.set_defaults(func=cmd_events)
    a = sub.add_parser("add", parents=[common], help="add a watch target to a record")
    a.add_argument("id", metavar="ID")
    a.add_argument("target", metavar="TARGET", help="git:<url> | github-user:<login> | github-releases:<owner/repo> | "
                                                    "page:<url> | feed:<url>")
    a.set_defaults(func=cmd_add)
    s = sub.add_parser("snapshot", parents=[common], help="save git archive of the watched repo's HEAD as an original")
    s.add_argument("id", metavar="ID")
    s.set_defaults(func=cmd_snapshot)
