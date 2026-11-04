# contrib

Scheduling `kb watch` on a Linux machine with systemd user units:

```sh
ln -sf "$PWD/kb-watch-all" ~/.local/bin/kb-watch-all
mkdir -p ~/.config/systemd/user && ln -sf "$PWD"/systemd/kb-watch.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now kb-watch.timer
loginctl enable-linger "$USER"        # keep user timers running when you're logged out
```

Every 15 minutes it runs `kb watch run --due --push` in each vault from `kb registry list` (shared vaults only); each record is checked at its own `watch_every` (`1h`, `12h`, `1d` …).
Watches commit only the record files they changed and push. A rejected push is rebased once; on a
conflict the commit stays local and the run fails (see `journalctl --user -u kb-watch`).
Put a read-only `GITHUB_TOKEN=...` in `~/.config/kb/env` for GitHub API checks (releases, new repos).

## claude-usage

Where Claude Code tokens and dollars go, from the transcripts in `~/.claude/projects` (plus a
token-only Codex section from `~/.codex/sessions`). Python 3.11 stdlib only.

```sh
ln -sf "$PWD/claude/claude-usage" ~/.local/bin/claude-usage
claude-usage top --days 30 --by project          # or --by session / --by model
mkdir -p ~/.config/systemd/user && ln -sf "$PWD"/systemd/claude-usage.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now claude-usage.timer
```

Every Monday at 06:00 (or at next boot if missed) it writes the last 7 days to
`~/.local/state/claude-usage/<ISO year>-W<week>.md`: totals, by project, top sessions, cost above
250k/400k context, cache rebuilds by cause, model mix and Opus subagents, repeat reads, idle
compactions. Dollars come from Claude Code's `cost-state` records, which it writes when a session
exits; sessions still running show as "no cost record" until they do.

## delegate

Runs a cheap OpenCode Go model through pi as an unattended worker: one git worktree and branch
(`delegate/<task>`) per task, a brief in, commits and a `result.md` out. Model chains are in
`delegate/chains.json`; the worker's extra instructions in `delegate/worker-rules.md`. Every run
is one JSON line in `~/.local/share/delegate/runs.jsonl`. At most 3 runs are live at once.
The worker runs under bubblewrap with an empty environment plus a short allowlist, and with
`~/creds`, `~/.ssh` and other login files hidden (see `HIDE` in the script); its commits are
unsigned. The Go key is read from `~/creds/opencode-go.env`. Python 3 stdlib only; needs `pi`
and `bwrap` on PATH.

```sh
ln -sf "$PWD/delegate/delegate" ~/.local/bin/delegate
delegate run --repo ~/src/x/y --task fix-parser --brief brief.md
delegate status
```
