Write a summary of this conversation so far for a fresh context that continues the work. It
replaces everything before the last two user prompts, which are kept word for word after it.

First, inside <analysis></analysis>, go through the conversation from the start in order: each
user message, what was done about it, the decisions, the errors and how they were fixed, and the
identifiers and numbers involved. Then write the summary inside <summary></summary>. Only the
summary is kept.

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
exact error messages, commands, ids and numbers that the next steps depend on, and every exact
identifier the work touched: hostnames, IP addresses, ports, versions, commit hashes, PR and issue
numbers, paths. Keep lessons learned: what went wrong, what fixed it, and the rule to follow next
time. Keep the caveats and warnings given to the user with their specifics (which commands, which
versions, what to test), and the user's options as they were put to them. Leave out secrets,
keys and passwords.

Length: as long as it needs to be, usually 3-8k tokens. Never drop a decision, a user
instruction or a running job to save space.
