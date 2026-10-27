"""kb sessions ingest: Claude Code and Codex transcripts -> session records (spec §2.12).

Raw transcripts stay outside git; only normalized, secret-redacted text and a record are written.
Git is only read (rev-parse, config, log), never written."""

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from kb import __version__, inject, secrets, vault as vaultmod
from kb.profile import load_profile
from kb.records import dump_front_matter, parse_excerpts, write_record
from kb.util import KBError, append_log, atomic_write, emit, excerpt_in_text, iso, parse_time, sha256_bytes, today

SOURCES = {"claude": ("~/.claude/projects", "*/*.jsonl"), "codex": ("~/.codex/sessions", "**/*.jsonl")}
RESULT_LINES = 20
CALL_CHARS = 160
DESC_PREFIX = "session on "        # generated descriptions contain this; others are the agent's and kept
# Codex injects these as role=user messages; they are context, not something the user typed.
CODEX_INJECTED = re.compile(r"\s*(?:<(?:environment_context|user_instructions|turn_aborted)\b|# AGENTS\.md instructions)")
INPUT_KEYS = ("command", "cmd", "file_path", "path", "pattern", "url", "query", "prompt", "description", "skill", "message")


# --- parsing ------------------------------------------------------------------------
# A parsed session: {"tool", "session_id", "cwd", "model", "entries": [(time, kind, text)], "bad": n}
# kind: user | assistant | call | result | result-error

def _first_line_of_input(inp) -> str:
    if isinstance(inp, str):
        try:
            parsed = json.loads(inp)
            inp = parsed if isinstance(parsed, dict) else inp
        except ValueError:
            pass
    if isinstance(inp, dict):
        inp = next((inp[k] for k in INPUT_KEYS if isinstance(inp.get(k), str) and inp[k].strip()), None) \
            or json.dumps(inp, ensure_ascii=False)
    line = next((l for l in str(inp).splitlines() if l.strip()), "").strip().replace("`", "'")
    return line if len(line) <= CALL_CHARS else line[:CALL_CHARS - 1] + "…"


def _blocks_text(content) -> str:
    """Text of a tool-result payload: a string, or a list of text/image/other blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict):
            if b.get("type") in ("image", "input_image"):
                parts.append("[image]")
            elif isinstance(b.get("text"), str):
                parts.append(b["text"])
    return "\n".join(parts)


def _lines(path):
    """Yield parsed JSON objects; None for a line that isn't a JSON object."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                yield None
                continue
            yield o if isinstance(o, dict) else None


def parse_claude(path) -> dict:
    s = {"tool": "claude", "session_id": "", "cwd": "", "model": "", "entries": [], "bad": 0}
    last = None
    for o in _lines(path):
        if o is None:
            s["bad"] += 1
            continue
        if o.get("type") not in ("user", "assistant"):
            continue                                    # metadata lines: known, ignored
        if o.get("isSidechain") or o.get("isMeta"):     # subagent turns / injected context
            continue
        t = parse_time(o.get("timestamp")) or last
        msg = o.get("message")
        if t is None or not isinstance(msg, dict):
            s["bad"] += 1
            continue
        last = t
        s["session_id"] = s["session_id"] or str(o.get("sessionId") or "")
        s["cwd"] = s["cwd"] or str(o.get("cwd") or "")
        model = msg.get("model")
        if not s["model"] and isinstance(model, str) and not model.startswith("<"):
            s["model"] = model
        role = o["type"]
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            s["bad"] += 1
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                s["entries"].append((t, role, b["text"]))
            elif bt == "image":
                s["entries"].append((t, role, "[image]"))
            elif bt == "tool_use":
                s["entries"].append((t, "call", f"{b.get('name', '?')}): {_first_line_of_input(b.get('input'))}"))
            elif bt == "tool_result":
                s["entries"].append((t, "result-error" if b.get("is_error") else "result", _blocks_text(b.get("content"))))
            # thinking, redacted_thinking and unknown blocks are omitted
    s["session_id"] = s["session_id"] or Path(path).stem
    return s


def parse_codex(path) -> dict:
    s = {"tool": "codex", "session_id": "", "cwd": "", "model": "", "entries": [], "bad": 0}
    last = None
    for o in _lines(path):
        if o is None:
            s["bad"] += 1
            continue
        typ = o.get("type")
        p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        t = parse_time(o.get("timestamp")) or last
        if typ == "session_meta" or (typ is None and "id" in o):   # current, and older first-line meta
            meta = p or o
            s["session_id"] = s["session_id"] or str(meta.get("id") or meta.get("session_id") or "")
            s["cwd"] = s["cwd"] or str(meta.get("cwd") or "")
            continue
        if typ == "turn_context":
            s["cwd"] = s["cwd"] or str(p.get("cwd") or "")
            s["model"] = s["model"] or str(p.get("model") or "")
            continue
        if typ != "response_item":
            if typ in ("message", "function_call", "function_call_output"):   # older rollouts: unwrapped items
                p = o
            else:
                continue                                 # event_msg, token counts, ...: known, ignored
        if t is None:
            s["bad"] += 1
            continue
        last = t
        pt = p.get("type")
        if pt == "message":
            role = p.get("role")
            if role not in ("user", "assistant") or not isinstance(p.get("content"), list):
                continue                                 # developer/system context
            for b in p["content"]:
                if not isinstance(b, dict):
                    continue
                if b.get("type") in ("input_image", "image"):
                    s["entries"].append((t, role, "[image]"))
                elif isinstance(b.get("text"), str) and b["text"].strip():
                    if role == "user" and CODEX_INJECTED.match(b["text"]):
                        continue
                    s["entries"].append((t, role, b["text"]))
        elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
            inp = p.get("arguments") if pt == "function_call" else p.get("input") if pt == "custom_tool_call" \
                else p.get("action")
            s["entries"].append((t, "call", f"{p.get('name') or 'shell'}): {_first_line_of_input(inp)}"))
        elif pt in ("function_call_output", "custom_tool_call_output"):
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("content", out.get("output", ""))
            s["entries"].append((t, "result", _blocks_text(out)))
        elif pt in ("reasoning", "agent_message"):
            continue
    s["session_id"] = s["session_id"] or Path(path).stem
    return s


def detect_tool(path) -> str | None:
    """'claude' or 'codex' from the first recognisable lines, else None."""
    for i, o in enumerate(_lines(path)):
        if i > 50:
            break
        if not o:
            continue
        if o.get("type") in ("session_meta", "response_item", "turn_context", "event_msg"):
            return "codex"
        if "sessionId" in o:
            return "claude"
    return None


# --- rendering ----------------------------------------------------------------------

def render(entries) -> str:
    out = []
    for t, kind, text in entries:
        a = f"[{t.astimezone(dt.timezone.utc):%H:%M}]"
        if kind in ("user", "assistant"):
            out.append(f"{a} **{kind}:** {text.strip()}")
        elif kind == "call":
            out.append(f"{a} `tool({text}`")
        else:
            lines = text.rstrip().splitlines()
            more = len(lines) - RESULT_LINES
            shown = "\n".join(lines[:RESULT_LINES]) + (f"\n… ({more} more lines)" if more > 0 else "")
            fence = "````" if "```" in shown else "```"
            label = "tool result (error)" if kind == "result-error" else "tool result"
            out.append(f"{a} {label}:\n{fence}\n{shown}\n{fence}" if shown.strip() else f"{a} {label}: (empty)")
    return "\n\n".join(out) + "\n"


def make_title(text: str, n: int = 80) -> str:
    t = " ".join(text.split())
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0] or t[:n]
    return cut + "…"


# --- git (read only) ------------------------------------------------------------------

def _git(*args) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_info(cwd: str, started: dt.datetime, ended: dt.datetime):
    """(repo_name, ["repo@hash7", ...]) for commits by this repo's user.email during the session."""
    if not cwd or not os.path.isdir(cwd):
        return "", []
    top = _git("-C", cwd, "rev-parse", "--show-toplevel")
    if not top:
        return "", []
    name = Path(top).name
    email = _git("-C", top, "config", "user.email")
    if not email:
        return name, []
    log = _git("-C", top, "log", "--all", f"--since={iso(started)}", f"--until={iso(ended + dt.timedelta(minutes=10))}",
               f"--author={email}", "--format=%H")
    return name, [f"{name}@{h[:7]}" for h in log.split()]


# --- skip rules -------------------------------------------------------------------------

def _under(path: str, roots) -> bool:
    if not path:
        return False
    p = vaultmod.expand(path)
    return any(p == r or r in p.parents for r in roots)


def local_vault_roots() -> list:
    roots = []
    for v in (vaultmod.load_registry().get("vaults") or {}).values():
        if not isinstance(v, dict) or not v.get("path"):
            continue
        root = vaultmod.expand(v["path"])
        access = v.get("access")
        if access is None and (root / "kb.yaml").is_file():
            try:
                access = load_profile(root).access
            except KBError:
                access = "local"                # can't tell: be safe
        if access == "local":
            roots.append(root)
    return roots


def _touches(raw: bytes, roots) -> bool:
    """Whether a local vault's path appears anywhere in the raw transcript (files read or written there)."""
    return any(str(r).encode() in raw for r in roots)


# --- ingest ---------------------------------------------------------------------------------

def find_files(tool: str, paths) -> list:
    files = []
    if paths:
        for p in paths:
            p = vaultmod.expand(p)
            files += sorted(p.rglob("*.jsonl")) if p.is_dir() else [p] if p.is_file() else []
        return files
    for name, (base, pattern) in SOURCES.items():
        if tool in (name, "all"):
            files += sorted(vaultmod.expand(base).glob(pattern))
    return files


def _abbrev(path: Path) -> str:
    home = Path.home()
    return "~/" + path.relative_to(home).as_posix() if home in path.parents else str(path)


def ingest(v, tool="all", since=None, dry_run=False, paths=()) -> dict:
    if "session" not in v.profile.types:
        raise KBError(f"vault {v.name!r} has no `session` type; ingest into an agent-vault "
                      f"(kb init --profile agent-vault) or copy the session type from that starter profile")
    td = v.profile.type("session")
    sess_cfg = v.profile.get("sessions") or {}
    excludes = [vaultmod.expand(p) for p in (sess_cfg.get("exclude") or [])]
    extra = (v.profile.get("secrets") or {}).get("extra_patterns") or []
    local_roots = local_vault_roots()
    since_t = parse_time(since) if since else None
    if since and since_t is None:
        raise KBError(f"--since {since!r} is not a date; use YYYY-MM-DD")

    rep = {"created": [], "updated": [], "unchanged": [], "skipped": {"local_vault": 0, "excluded": 0, "empty": 0,
           "unknown_format": 0, "before_since": 0}, "bad_lines": 0, "excerpt_problems": [], "dry_run": dry_run}
    for f in find_files(tool, paths):
        if since_t and dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc) < since_t:
            rep["skipped"]["before_since"] += 1
            continue
        kind = detect_tool(f)
        if kind is None:
            rep["skipped"]["unknown_format"] += 1
            continue
        if tool not in ("all", kind):
            continue
        raw = f.read_bytes()
        s = parse_claude(f) if kind == "claude" else parse_codex(f)
        rep["bad_lines"] += s["bad"]
        if _under(s["cwd"], local_roots) or _touches(raw, local_roots):
            rep["skipped"]["local_vault"] += 1
            continue
        if _under(s["cwd"], excludes):
            rep["skipped"]["excluded"] += 1
            continue
        if not any(k in ("user", "assistant") for _, k, _ in s["entries"]):
            rep["skipped"]["empty"] += 1
            continue
        started, ended = s["entries"][0][0], max(e[0] for e in s["entries"])
        if since_t and ended < since_t:
            rep["skipped"]["before_since"] += 1
            continue
        sid8 = re.sub(r"[^0-9A-Za-z]", "", s["session_id"])[:8] or "unknown"
        rid = f"session-{started.astimezone(dt.timezone.utc):%Y-%m-%d}-{kind}-{sid8}"
        _ingest_one(v, td, rid, f, raw, s, started, ended, extra, dry_run, rep)
    if not dry_run:
        sk = sum(rep["skipped"].values())
        append_log(v.root, "sessions", "ingest", f"created {len(rep['created'])}, updated {len(rep['updated'])}, "
                   f"unchanged {len(rep['unchanged'])}, skipped {sk}")
    return rep


def _ingest_one(v, td, rid, f, raw, s, started, ended, extra, dry_run, rep):
    raw_sha = sha256_bytes(raw)
    existing = v.get(rid)
    rec_path = existing.path if existing else v.root / td.folder_for() / f"{rid}.md"
    if existing and raw_sha in str(existing.fm.get("originals_note", "")):
        rep["unchanged"].append(rid)
        return
    rep["updated" if existing else "created"].append(rid)
    if dry_run:
        return

    body_text, _ = secrets.redact(render(s["entries"]), extra)
    text_sha = sha256_bytes(body_text.encode("utf-8"))
    text_rel = f"text/{rid}.md"
    text_fm = {"id": rid, "extracted_with": f"kb sessions {__version__}", "text_quality": "native",
               "anchors": "timestamp", "text_sha256": text_sha}
    atomic_write(v.root / text_rel, dump_front_matter(text_fm) + "\n" + body_text)

    first_user = next((t for _, k, t in s["entries"] if k == "user" and t != "[image]"), "")
    title = make_title(secrets.redact(first_user, extra)[0]) or f"{s['tool']} session {started:%Y-%m-%d}"
    turns = sum(1 for _, k, t in s["entries"] if k == "user" and t != "[image]")
    where = Path(s["cwd"]).name if s["cwd"] else "unknown directory"
    desc = f"{s['tool'].capitalize()} {DESC_PREFIX}{started:%Y-%m-%d} in {where}, {turns} user turn{'s' * (turns != 1)}."
    repo, commits = git_info(s["cwd"], started, ended)
    flags = inject.scan(body_text)

    gen = {"tool": s["tool"], "session_id": s["session_id"], "started": iso(started), "ended": iso(ended),
           "cwd": s["cwd"], "repo": repo, "model": s["model"], "commits": commits, "originals": [],
           "originals_note": f"raw transcript kept outside git: {_abbrev(f)} sha256:{raw_sha}",
           "text": text_rel, "text_sha256": text_sha, "extracted_with": text_fm["extracted_with"],
           "text_quality": "native", "updated": today()}
    gen = {k: val for k, val in gen.items() if val not in ("", None) or k in ("originals",)}
    if existing:
        fm = dict(existing.fm)
        for k in ("repo", "model", "injection_flag", "injection_note"):
            fm.pop(k, None)
        if DESC_PREFIX in str(fm.get("description", "")):
            fm["description"] = desc
        fm.update(gen)
        body = existing.body
    else:
        fm = {"type": "session", "title": title, "description": desc, **gen}
        body = f"\n# {title}\n\n## Summary\n\n## Excerpts\n\n## Notes\n"
    if flags:
        fm["injection_flag"] = True
        fm["injection_note"] = "; ".join(flags)
    write_record(rec_path, fm, body)
    if existing:
        for e in parse_excerpts(body):
            if e.withdrawn or e.gone:
                continue
            m = excerpt_in_text(e.text, body_text)
            if m != "exact":
                rep["excerpt_problems"].append({"id": rid, "excerpt": e.eid, "match": m})


# --- CLI --------------------------------------------------------------------------------------

def handler(args) -> int:
    v = vaultmod.open_from_args(args)
    rep = ingest(v, args.tool, args.since, args.dry_run, args.path or ())

    def text(r):
        verb = "would " if r["dry_run"] else ""
        sk = ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in r["skipped"].items() if n)
        lines = [f"{verb}create {len(r['created'])}, {verb}update {len(r['updated'])}, unchanged {len(r['unchanged'])}"
                 + (f"; skipped: {sk}" if sk else "") + (f"; bad lines {r['bad_lines']}" if r["bad_lines"] else "")]
        lines += [f"  + {i}" for i in r["created"]] + [f"  ~ {i}" for i in r["updated"]]
        if r["dry_run"]:
            lines += [f"  = {i}" for i in r["unchanged"]]
        lines += [f"  ! {p['id']}#{p['excerpt']}: excerpt {p['match']} in the new text; fix or mark it Withdrawn"
                  for p in r["excerpt_problems"]]
        return lines

    emit(args, rep, text)
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("sessions", parents=[common], help="ingest Claude Code / Codex agent sessions")
    sub = p.add_subparsers(dest="sessions_cmd", metavar="SUBCOMMAND", required=True)
    ing = sub.add_parser("ingest", parents=[common], help="write session records and redacted text from transcripts")
    ing.add_argument("--tool", choices=["claude", "codex", "all"], default="all")
    ing.add_argument("--since", metavar="DATE", help="only sessions active on or after DATE (YYYY-MM-DD)")
    ing.add_argument("--dry-run", action="store_true", help="list what would be created/updated, write nothing")
    ing.add_argument("--path", action="append", metavar="P",
                     help="transcript file or directory instead of the default locations (repeatable)")
    ing.set_defaults(func=handler)
