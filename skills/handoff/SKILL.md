---
name: handoff
description: Write a handoff of this session so a fresh session can pick up the work. Use when the user runs /handoff or asks to hand off, restart clean, or switch work in this terminal.
argument-hint: "What will the next session work on?"
disable-model-invocation: true
---

Write a handoff of the current session, then tell the user how to continue from it.

1. If the user passed arguments, they say what the next session will work on; shape the handoff
   around that.
2. Write it following the checklist below to
   `~/.local/state/claude-handoffs/<YYYYMMDD-HHMMSS>-<slug>.md`, where `<slug>` is the current
   working directory with the leading `/` removed and the other `/` replaced by `-`.
3. Write the handoff's path (as `~/.local/state/...`) to
   `~/.local/state/claude-handoffs/pending-<slug>`, replacing any earlier one.
4. Tell the user in one line: the handoff path, and "run `/clear`; the new session loads it" (in
   Claude Code, a session-start hook loads it). In other agents, tell them to start a new session
   and give it the path.

## Checklist

Keep it under ~2k tokens. Point to design docs, commits, diffs and issues by path or URL instead
of restating them. Leave out secrets, keys and passwords.

- **Goal:** what the session is for and what the user asked for, in their words where wording
  matters.
- **Decisions:** what was decided and why, one line each, with links to the design doc or commit.
- **State:** repo and branch, uncommitted changes, anything running (servers, background agents,
  timers) and how to check on it.
- **Next steps:** in order, concrete enough to start without asking.
- **Open questions:** anything waiting on the user.
- **Files to re-read:** paths, with line ranges where useful.
- **Skills:** which skills the next session should load.
