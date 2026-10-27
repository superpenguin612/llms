"""kb queue: the discover queue in inbox/discover-<date>.md (candidate sources awaiting a tick)."""

import importlib
import re
from pathlib import Path

from kb.util import KBError, append_log, atomic_write, emit, normalize, today
from kb.vault import _norm_url, open_from_args

# - [ ] [title](url) — reason        states: ' ' pending, 'x' ticked, '-' rejected
ITEM = re.compile(r"^(?P<pre>\s*[-*] \[)(?P<state>[ xX-])(?P<mid>\] \[(?P<title>.*?)\]\((?P<url>[^)\s]+)\))(?P<rest>.*)$")
HEADER = ("# Discover queue {date}\n\n"
          "Tick `- [x]` to capture (`kb queue capture`); reject with `kb queue reject URL --reason R`.\n\n")


def _files(vault) -> list[Path]:
    return sorted((vault.root / "inbox").glob("discover-*.md"))


def items(vault):
    """(path, line_no, match) for every queue item in every discover file."""
    for f in _files(vault):
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n")):
            m = ITEM.match(line.rstrip("\r"))
            if m:
                yield f, i, m


def _state(m) -> str:
    s = m.group("state").lower()
    if s == "-":
        return "rejected"
    if s == "x":
        return "captured" if "→ [[" in m.group("rest") else "ticked"
    return "pending"


def _find(vault, url):
    norm = _norm_url(url)
    return next(((f, i, m) for f, i, m in items(vault) if _norm_url(m.group("url")) == norm), None)


def _append(vault, line: str) -> Path:
    path = vault.root / "inbox" / f"discover-{today()}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER.format(date=today())
    atomic_write(path, text.rstrip("\n") + "\n" + line + "\n" if path.exists() else text + line + "\n")
    return path


def _set_line(path: Path, i: int, line: str) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[i] = line
    atomic_write(path, "\n".join(lines))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("[", "(").replace("]", ")")).strip()


def add(vault, url, reason="", query="", title="") -> dict:
    if not re.match(r"https?://\S+$", url or ""):
        raise KBError(f"not an http(s) URL: {url!r}")
    rec = vault.find_by_url(url)
    if rec is None and title:
        rec = next((r for r in vault.records if normalize(r.title) == normalize(title)), None)
    if rec is not None:
        return {"added": False, "url": url, "reason": f"already in the vault as [[{rec.id}]]"}
    hit = _find(vault, url)
    if hit:
        return {"added": False, "url": url, "reason": f"already queued ({_state(hit[2])}) in {vault.rel(hit[0])}"}
    line = f"- [ ] [{_clean(title) or url}]({url})" + (f" — {_clean(reason)}" if reason else "") \
        + (f" (query: {_clean(query)})" if query else "")
    return {"added": True, "url": url, "file": vault.rel(_append(vault, line))}


def capture(vault) -> dict:
    try:
        cap = importlib.import_module("kb.capture")
    except ImportError:
        raise KBError("kb capture is not available in this install; capture ticked items by hand with kb capture URL") from None
    done, failed = [], []
    for f, i, m in list(items(vault)):
        if _state(m) != "ticked":
            continue
        try:
            rec = cap.capture_url(vault, m.group("url"), obtained_from=f"discover queue {f.name}")
        except KBError as e:
            failed.append({"url": m.group("url"), "error": str(e)})
            continue
        _set_line(f, i, m.group(0).rstrip() + f" → [[{rec.id}]]")  # save after each, so a rerun resumes
        done.append({"url": m.group("url"), "id": rec.id})
    return {"captured": done, "failed": failed}


def reject(vault, url, reason) -> dict:
    if not (reason or "").strip():
        raise KBError("give a reason: kb queue reject URL --reason R")
    tail = f" — rejected: {_clean(reason)}"
    hit = _find(vault, url)
    if hit is None:
        path = _append(vault, f"- [-] [{url}]({url}){tail}")
    else:
        path, i, m = hit
        _set_line(path, i, f"{m.group('pre')}-{m.group('mid')}{m.group('rest').rstrip()}{tail}")
    return {"url": url, "file": vault.rel(path)}


def cmd_add(args) -> int:
    r = add(open_from_args(args), args.url, args.reason or "", args.query or "", args.title or "")
    emit(args, r, f"queued {r['url']} in {r['file']}" if r["added"] else f"skipped {r['url']}: {r['reason']}")
    return 0


def cmd_list(args) -> int:
    v = open_from_args(args)
    rows = [{"state": _state(m), "title": m.group("title"), "url": m.group("url"), "file": v.rel(f),
             "note": m.group("rest").strip(" —")} for f, i, m in items(v)]
    if not args.all:
        rows = [r for r in rows if r["state"] in ("pending", "ticked")]
    emit(args, rows, [f"[{r['state']}] {r['title']} <{r['url']}> {r['note']}".rstrip() for r in rows]
         or "queue is empty")
    return 0


def cmd_log_search(args) -> int:
    v = open_from_args(args)
    detail = f"{args.results} result(s)" + (f"; {args.note}" if args.note else "")
    append_log(v.root, "search", args.query, detail)
    emit(args, {"query": args.query, "results": args.results, "note": args.note or ""}, f"logged search: {args.query}")
    return 0


def cmd_capture(args) -> int:
    r = capture(open_from_args(args))
    emit(args, r, [f"captured {c['url']} → [[{c['id']}]]" for c in r["captured"]]
         + [f"failed {c['url']}: {c['error']}" for c in r["failed"]] or "nothing ticked")
    return 1 if r["failed"] else 0


def cmd_reject(args) -> int:
    r = reject(open_from_args(args), args.url, args.reason)
    emit(args, r, f"rejected {r['url']} in {r['file']}")
    return 0


def register(subparsers, common) -> None:
    p = subparsers.add_parser("queue", parents=[common], help="discover queue: candidate sources to capture")
    sub = p.add_subparsers(dest="queue_command", metavar="SUBCOMMAND")
    a = sub.add_parser("add", parents=[common], help="queue a URL (deduped against the vault and the queue)")
    a.add_argument("url")
    a.add_argument("--reason", help="one line: why it matters")
    a.add_argument("--query", help="the search query that found it")
    a.add_argument("--title")
    a.set_defaults(func=cmd_add)
    ls = sub.add_parser("list", parents=[common], help="pending and ticked items")
    ls.add_argument("--all", action="store_true", help="include captured and rejected items")
    ls.set_defaults(func=cmd_list)
    s = sub.add_parser("log-search", parents=[common], help="record a search in log.md")
    s.add_argument("--query", required=True)
    s.add_argument("--results", required=True, type=int)
    s.add_argument("--note")
    s.set_defaults(func=cmd_log_search)
    c = sub.add_parser("capture", parents=[common], help="capture every ticked item")
    c.set_defaults(func=cmd_capture)
    r = sub.add_parser("reject", parents=[common], help="mark a URL rejected so it is not queued again")
    r.add_argument("url")
    r.add_argument("--reason", required=True)
    r.set_defaults(func=cmd_reject)
    p.set_defaults(func=lambda args: (p.print_help(), 0)[1])
