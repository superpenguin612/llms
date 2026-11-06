Write a summary of this conversation so far for a fresh context that continues the work. It
replaces everything before the last two user prompts, which are kept word for word after it.

Include, in this order, with these headings:
- **Goal:** what the session is for and what the user asked for. Quote the user's exact words
  for every request, decision, preference or correction that matters.
- **Decisions:** what was decided and why, one line each, with the commit, design doc or file
  that records it.
- **State:** repo and branch, uncommitted changes, and every running background thing (servers,
  background shell commands, subagents, delegate runs, crons, /loop or scheduled wakeups,
  tmux sessions): what it is, its id, and how to check on it or stop it.
- **Next steps:** in order, concrete enough to start without asking.
- **Open questions:** anything waiting on the user, in the user's terms.
- **Files to re-read:** paths, with line ranges where useful.
- **Skills:** which skills were in use and should be loaded again.
- **Constraints kept:** rules the user stated or the session relied on.

Point to commits, design docs, diffs and issues by path or URL instead of restating them. Keep
exact error messages, commands, ids and numbers that the next steps depend on. Leave out secrets,
keys and passwords.

Length: as long as it needs to be, usually 3-8k tokens. Never drop a decision, a user
instruction or a running job to save space.
