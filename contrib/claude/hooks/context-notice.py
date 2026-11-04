#!/usr/bin/env python3
"""Claude Code UserPromptSubmit: tell the user (not the model) when the context crosses 250k or 400k.

It reads the last reply's usage from the transcript, so it reports the context as of the previous
turn. Each notice fires once per crossing; dropping back below (after a compaction) re-arms it."""
import json, os, sys

THRESHOLDS = (250000, 400000)
STATE = os.path.expanduser("~/.cache/claude-context-notice")
TAIL = 512 * 1024  # the last reply is near the end; usage lines are small

def last_context(path):
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - TAIL))
        lines = f.read().splitlines()
    for line in reversed(lines):
        if b'"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        u = (d.get("message") or {}).get("usage")
        if d.get("type") == "assistant" and isinstance(u, dict) and not d.get("isSidechain"):
            return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return None

def main():
    data = json.load(sys.stdin)
    tp, sid = data.get("transcript_path"), data.get("session_id")
    if not tp or not sid or not os.path.exists(tp):
        return
    ctx = last_context(tp)
    if ctx is None:
        return
    key = os.path.join(STATE, sid + ".json")
    try:
        fired = set(json.load(open(key)))
    except (OSError, ValueError):
        fired = set()
    new = [t for t in THRESHOLDS if ctx >= t and t not in fired]
    fired = {t for t in THRESHOLDS if ctx >= t}
    os.makedirs(STATE, exist_ok=True)
    json.dump(sorted(fired), open(key, "w"))
    if new:
        print(json.dumps({"systemMessage": "Context window has reached %dk tokens." % (max(new) // 1000)}))

if __name__ == "__main__":
    main()
