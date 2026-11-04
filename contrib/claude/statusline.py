#!/usr/bin/env python3
import json, os, subprocess, sys, time
from datetime import datetime

def fg(n, s):
    return "\x1b[38;5;%dm%s\x1b[0m" % (n, s)

YELLOW, GREEN, PURPLE, DIM, EMPTY = 222, 114, 183, 240, 238
RED, CTX_GREEN, CTX_YELLOW = 203, 114, 222
SEP = fg(DIM, " · ")

def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def bar(pct, color, width=8):
    n = max(0, min(width, int(round(pct / 100.0 * width))))
    return fg(color, "█" * n) + fg(EMPTY, "░" * (width - n))

def until(ts):
    secs = int(ts - time.time())
    if secs <= 0:
        return None
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return "%dd%02dh" % (d, h)
    if h:
        return "%dh%02dm" % (h, m)
    return "%dm" % m

def limit(label, win, color, show_reset):
    if not isinstance(win, dict):
        return None
    pct = num(win.get("used_percentage"))
    if pct is None:
        return None
    out = "%s %s %d%%" % (fg(color, label), bar(pct, color), round(pct))
    if show_reset:
        reset = num(win.get("resets_at"))
        t = until(reset) if reset is not None else None
        if t:
            out += " in " + t
    return out

def git_info(cwd):
    try:
        r = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain", "-b"],
            capture_output=True, text=True, timeout=0.5,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.startswith("##"):
        return None
    lines = r.stdout.split("\n")
    head = lines[0][3:]
    if head.startswith("No commits yet on "):
        branch = head[len("No commits yet on "):]
    elif head.startswith("HEAD (no branch)"):
        branch = "detached"
    else:
        branch = head.split("...")[0]
    dirty = any(l.strip() for l in lines[1:])
    return branch + ("*" if dirty else "")


CACHE_DIR = os.path.expanduser("~/.cache/claude-statusline")
CACHE_TTL = 3600  # main sessions use the 1 h prompt cache
WARN, DANGER = 250000, 400000  # same thresholds as the context-notice hook

def _scan(path, st):
    """Keep the last reply's usage and time, reading only bytes appended since the last call."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return st
    if st.get("offset", 0) > size:  # file replaced: start over
        st = {}
    off = st.get("offset", 0)
    if off == size:
        return st
    with open(path, "rb") as f:
        f.seek(off)
        chunk = f.read(size - off)
    end = chunk.rfind(b"\n") + 1  # only complete lines
    st = dict(st, offset=off + end)
    for line in chunk[:end].splitlines():
        if b'"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        u = (d.get("message") or {}).get("usage")
        if d.get("type") == "assistant" and isinstance(u, dict) and not d.get("isSidechain"):
            st["usage"], st["ts"] = u, d.get("timestamp")
    return st

def last_turn(data, usd):
    """(usage, unix time) of the session's last reply, or None. Saves the live cost for claude-usage,
    since Claude Code records a session's cost only when it exits."""
    tp = data.get("transcript_path")
    if not isinstance(tp, str) or not os.path.exists(tp):
        return None
    key = os.path.join(CACHE_DIR, "%s.json" % (data.get("session_id") or os.path.basename(tp)))
    try:
        st = json.load(open(key))
    except Exception:
        st = {}
    st = _scan(tp, st)
    if usd is not None:
        st["cost"] = usd
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump(st, open(key + ".tmp", "w"))
        os.replace(key + ".tmp", key)
    except Exception:
        pass
    u, ts = st.get("usage"), st.get("ts")
    if not isinstance(u, dict) or not isinstance(ts, str):
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return u, t

def human(n):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return ("%.1f" % (n / div)).rstrip("0").rstrip(".") + suf
    return str(int(n))

def turn_segments(turn, now):
    u, t = turn
    read = num(u.get("cache_read_input_tokens")) or 0
    ctx = read + (num(u.get("cache_creation_input_tokens")) or 0) + (num(u.get("input_tokens")) or 0)
    c = CTX_GREEN if ctx < WARN else CTX_YELLOW if ctx < DANGER else RED
    out = [fg(c, "ctx " + human(ctx))]
    left = int(t + CACHE_TTL - now)
    out.append(fg(RED, "cache cold") if left <= 0 else fg(RED if left < 600 else DIM, "cache %dm" % (left // 60)))
    if ctx:
        out.append(fg(DIM, "hit %d%%" % round(100.0 * read / ctx)))
    return out

def main():
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    parts = []

    rl = data.get("rate_limits") if isinstance(data.get("rate_limits"), dict) else {}
    for seg in (limit("5h", rl.get("five_hour"), YELLOW, True),
                limit("7d", rl.get("seven_day"), GREEN, False)):
        if seg:
            parts.append(seg)

    model = data.get("model")
    name = model.get("display_name") if isinstance(model, dict) else None
    eff = data.get("effort")
    level = eff.get("level") if isinstance(eff, dict) else None
    if name:
        parts.append(fg(PURPLE, name + (" " + level if isinstance(level, str) else "")))

    cost = data.get("cost")
    usd = num(cost.get("total_cost_usd")) if isinstance(cost, dict) else None
    try:
        turn = last_turn(data, usd)
    except Exception:
        turn = None
    if turn:
        parts += turn_segments(turn, time.time())

    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    cwd = ws.get("current_dir") or data.get("cwd")
    if isinstance(cwd, str) and cwd:
        seg = os.path.basename(cwd.rstrip("/")) or "/"
        g = git_info(cwd)
        if g:
            seg += " " + fg(DIM, "(") + g + fg(DIM, ")")
        parts.append(seg)

    if usd is not None:
        parts.append("$%.2f" % usd)

    sys.stdout.write(SEP.join(parts))

main()
