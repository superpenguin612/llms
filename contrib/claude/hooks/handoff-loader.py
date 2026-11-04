#!/usr/bin/env python3
"""Claude Code SessionStart (clear): load the handoff that /handoff left for this directory.

The handoff skill writes ~/.local/state/claude-handoffs/<time>-<slug>.md and a pending-<slug>
pointer to it. A pointer older than MAX_AGE is ignored and removed.
shortcut: keyed by cwd, so two panes in one directory handing off at once would cross; key by
tmux pane if that happens."""
import json, os, sys, time

DIR = os.path.expanduser("~/.local/state/claude-handoffs")
MAX_AGE = 2 * 3600

def main():
    data = json.load(sys.stdin)
    cwd = data.get("cwd") or os.getcwd()
    ptr = os.path.join(DIR, "pending-" + cwd.strip("/").replace("/", "-"))
    try:
        age = time.time() - os.path.getmtime(ptr)
        target = open(ptr).read().strip()
    except OSError:
        return
    os.remove(ptr)
    if age > MAX_AGE:
        return
    try:
        text = open(os.path.expanduser(target)).read()
    except OSError:
        return
    ctx = "Handoff from the previous session (%s):\n\n%s" % (target, text)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))

if __name__ == "__main__":
    main()
