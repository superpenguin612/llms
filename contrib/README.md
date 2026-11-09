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
delegate run --repo ~/src/x/y --task fix-parser --brief brief.md   # --here: in place, no worktree
delegate status
delegate retry fix-parser --feedback review.md   # once; continues the worker's pi session
delegate clean fix-parser                        # after merging; --force abandons, --opus = taken over
```

On a timeout or API error the next model in the chain starts over from the base commit, and the
failed attempt's commits stay on `delegate/<task>@<model>` until `clean`. Run folders are kept.

## pdfpane

Shows a PDF page or an image in a tmux pane beside the current one, drawn with `timg` as sixel
(tmux 3.4+ passes sixel through natively; iTerm2, WezTerm and foot draw it). Running it again
reuses the pane, so an agent can point it at whatever it just built. The pane redraws when the file
changes, so it follows rebuilds. Needs `timg` and `pdfinfo` (`apt install timg poppler-utils`).

```sh
ln -sf "$PWD/pdfpane" ~/.local/bin/pdfpane
pdfpane build/report.pdf 3        # opens page 3 beside the current pane
```

Keys in the pane: `n`/space/→/PgDn and `p`/←/PgUp for next and previous page, `g`/`G` first and last,
`:` then a number to jump, `+`/`-` zoom, `0` fit page, `w` fit width, `h j k l` (or ↑ ↓) to
pan (capitals pan a whole screen), `<`/`>` widen or narrow the pane, `f` full screen on and off,
`r` redraw, `q` close. Zoomed views are re-rendered from the PDF
at the new resolution (`pdftoppm` with a crop), so text stays sharp. Images need Pillow.
