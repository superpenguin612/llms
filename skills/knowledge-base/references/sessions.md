# Agent sessions

In a vault with a `session` type (the agent-vault profile), agent transcripts become records, so designs and project notes can cite the exact moment something was decided.

## Ingest

```sh
kb sessions ingest --tool all --since 2026-10-01 --dry-run   # see what would change
kb sessions ingest --tool all --since 2026-10-01
```

- **Sources:** Claude Code (`~/.claude/projects/`) and Codex (`~/.codex/sessions/`).
- **Each session becomes:**
  - `sessions/<id>.md`;
  - a normalized transcript `text/<id>.md`, with secrets redacted and `[HH:MM]` anchors.
- **The raw transcript stays out of git;** only its hash is recorded.
- **`commits:`** lists the commits made in the session's repo during the session. This mapping lives only in the vault. Commit messages are never changed.
- **Skipped:** sessions that ran inside a `local` vault or an excluded path.
- **Re-running is safe.** Unchanged sessions are skipped. Grown sessions get their text regenerated, and your summary and excerpts are kept.

## Summarize (your part)

For each new session record:

1. Read the transcript: `kb show <id> --text`.
2. **`## Summary`:** 3-6 sentences covering what was worked on, what was decided and why, what was left open.
3. **`## Excerpts`:** quote the turns that carry decisions, corrections and stated preferences verbatim, from the user's own words where possible. Each gets a `Location: [HH:MM]` line.
4. **Link** the session to its `project` and to any `design` it informed (`project: "[[llms]]"`). In the design doc, cite the excerpt where a decision was made (`[[session-…#e3]]`).
5. **Durable preferences or rules** stated in a session belong in the right `AGENTS.md` file (global or repo), not only in the session record. Propose the edit to the user.
