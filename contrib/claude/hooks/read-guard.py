#!/usr/bin/env python3
"""Claude Code PreToolUse (Read) and SessionStart (compact): warn, never block, on wasteful reads.

Warns when a whole file is read again unchanged in the same session, or when a whole text file is
very large. Compaction drops file contents, so SessionStart(compact) forgets what was read."""
import json, os, sys

STATE = os.path.expanduser("~/.cache/claude-read-guard")
BIG = 100 * 1024  # ~25k tokens of text
SKIP = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ipynb"}  # size isn't their token cost

def main():
    data = json.load(sys.stdin)
    sid = data.get("session_id")
    if not sid:
        return
    key = os.path.join(STATE, sid + ".json")
    if data.get("hook_event_name") == "SessionStart":
        try:
            os.remove(key)
        except OSError:
            pass
        return
    inp = data.get("tool_input") or {}
    path = inp.get("file_path")
    if not path or inp.get("offset") or inp.get("limit") or os.path.splitext(path)[1].lower() in SKIP:
        return
    try:
        st = os.stat(path)
    except OSError:
        return
    try:
        seen = json.load(open(key))
    except (OSError, ValueError):
        seen = {}
    stamp = [st.st_mtime_ns, st.st_size]
    note = None
    if seen.get(path) == stamp:
        note = "%s is unchanged since you read it earlier in this session; use what you have, or read only the range you need." % path
    elif st.st_size > BIG:
        note = "%s is %d KB; grep it or read only the range you need instead of the whole file." % (path, st.st_size // 1024)
    seen[path] = stamp
    os.makedirs(STATE, exist_ok=True)
    json.dump(seen, open(key, "w"))
    if note:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}}))

if __name__ == "__main__":
    main()
