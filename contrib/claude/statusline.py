#!/usr/bin/env python3
import json, os, subprocess, sys, time

def fg(n, s):
    return "\x1b[38;5;%dm%s\x1b[0m" % (n, s)

YELLOW, GREEN, PURPLE, DIM, EMPTY = 222, 114, 183, 240, 238
RED, CTX_GREEN, CTX_YELLOW, CYAN, LIGHT = 203, 114, 222, 117, 252
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
WARN, DANGER = 250000, 400000  # same thresholds as the context-notice hook

def save_cost(data, usd):
    """Claude Code records a session's cost only when it exits; keep the live figure for claude-usage."""
    sid = data.get("session_id")
    if not isinstance(sid, str) or usd is None:
        return
    key = os.path.join(CACHE_DIR, sid + ".json")
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump({"cost": usd}, open(key + ".tmp", "w"))
        os.replace(key + ".tmp", key)
    except Exception:
        pass

def human(n):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return ("%.1f" % (n / div)).rstrip("0").rstrip(".") + suf
    return str(int(n))

def obj(data, key):
    v = data.get(key)
    return v if isinstance(v, dict) else {}

def context_segment(data):
    u = obj(obj(data, "context_window"), "current_usage")  # null before the first reply and after /compact
    if not u:
        return None
    ctx = sum(num(u.get(k)) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return fg(CTX_GREEN if ctx < WARN else CTX_YELLOW if ctx < DANGER else RED, "ctx " + human(ctx))

def cache_segments(data, now):
    pc = obj(data, "prompt_cache")
    out = []
    exp, cold = num(pc.get("expires_at")), num(pc.get("recache_tokens_if_cold"))
    # current_usage is null right after a compaction; prompt_cache still describes the old prefix then
    if exp is not None and obj(obj(data, "context_window"), "current_usage"):
        left = int(exp - now)
        if left <= 0 or pc.get("warm") is False:
            out.append(fg(RED, "cache cold" + (": %s" % human(cold) if cold else "")))
        else:
            # The clock time stays right between redraws; the countdown alone went stale while idle.
            seg = "cache till %s" % time.strftime("%H:%M", time.localtime(exp))
            if left < 300 and cold:
                seg += " (cold: %s)" % human(cold)
            out.append(fg(RED if left < 600 else CYAN, seg))
    hit = num(pc.get("hit_ratio"))
    if hit is not None:
        out.append(fg(LIGHT, "hit %d%%" % round(100 * hit)))
    misses = num(pc.get("misses"))
    if misses:
        causes = obj(pc, "last_miss_cause").get("causes")
        why = causes[0] if isinstance(causes, list) and causes and isinstance(causes[0], str) else None
        out.append(fg(RED, "%d miss%s%s" % (misses, "" if misses == 1 else "es",
                                            ": " + why.replace("_", " ") if why else "")))
    return out

def main():
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    parts = []

    model = data.get("model")
    name = model.get("display_name") if isinstance(model, dict) else None
    eff = data.get("effort")
    level = eff.get("level") if isinstance(eff, dict) else None
    if name:
        parts.append(fg(PURPLE, name + (" " + level if isinstance(level, str) else "")))

    rl = data.get("rate_limits") if isinstance(data.get("rate_limits"), dict) else {}
    for seg in (limit("5h", rl.get("five_hour"), YELLOW, True),
                limit("7d", rl.get("seven_day"), GREEN, False)):
        if seg:
            parts.append(seg)

    cost = obj(data, "cost")
    usd = num(cost.get("total_cost_usd"))
    save_cost(data, usd)
    seg = context_segment(data)
    if seg:
        parts.append(seg)
    parts += cache_segments(data, time.time())

    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    cwd = ws.get("current_dir") or data.get("cwd")
    if isinstance(cwd, str) and cwd:
        seg = os.path.basename(cwd.rstrip("/")) or "/"
        g = git_info(cwd)
        if g:
            seg += " " + fg(DIM, "(") + g + fg(DIM, ")")
        parts.append(seg)

    pr = obj(data, "pr")
    if num(pr.get("number")):
        parts.append("#%d" % pr["number"] + (" " + pr["review_state"].replace("_", " ") if isinstance(pr.get("review_state"), str) else ""))
    for label in (obj(data, "worktree").get("name"), obj(data, "agent").get("name")):
        if isinstance(label, str) and label:
            parts.append(fg(PURPLE, label))
    added, removed = num(cost.get("total_lines_added")) or 0, num(cost.get("total_lines_removed")) or 0
    if added or removed:
        parts.append(fg(CTX_GREEN, "+%d" % added) + fg(DIM, "/") + fg(RED, "-%d" % removed))

    if usd is not None:
        parts.append("$%.2f" % usd)

    sys.stdout.write(SEP.join(parts))

main()
