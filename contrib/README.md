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

## agent-ping

Marks the tmux window where Claude Code or Codex is waiting on me (a permission prompt, a question,
a finished turn), keeps a count of waiting windows, and sends a banner to my Mac. POSIX sh + jq;
always exits 0, prints nothing, takes ~30 ms, and does nothing outside tmux.

- Reasons: `ask` (Claude's `AskUserQuestion` dialog via PreToolUse, an MCP `elicitation_dialog`,
  or `agent-ping ask TEXT` run by any agent), `permission`, `idle` (Claude's 60 s idle notice) and
  `done` (`Stop`). Sets the window option `@agent_waiting` to `<epoch>|<reason>`, and the globals
  `@agents_asking` (windows with an ask) and `@agents_waiting` (the rest), for the status bar. An
  ask is never replaced by a later permission, idle or done mark; a prompt or `SessionEnd` clears it.
- Banners: asks and permission prompts always; a finished turn only when it ran 2 minutes or more
  (counted from the prompt), with the first line of the reply. For an iTerm2 client
  (`#{client_termtype}`) it writes `OSC 1337 ; Custom=id=agent-ping:<base64 {title, subtitle,
  body}>`, which the dotfiles' iTerm2 script `agent_ping.py` posts as a macOS notification; other
  terminals get OSC 9 `<title>: <body>`. Every client also gets a 5 s status-line message. Nothing is
  marked or sent when the pane is the active pane of a window a client is showing.
- A prompt clears the window's mark (any prompt in the window: one mark per window, newest reason
  wins), as does Claude's `SessionEnd`. A prompt starting with `keepalive:` (the `idle` mod) leaves
  the mark alone, and the `Stop` that follows it is ignored.
- Records `<agent>:<session_id>` on the pane (`@agent_session`) and in
  `~/.local/state/agent-ping/panes.tsv` (session, window, pane, agent, id; one row per pane).
  Quitting Claude (`/exit`, logout) drops the row; a kill or reboot keeps it. After a tmux-resurrect
  restore, `agent-ping restore` types `claude --resume <id>` or `codex resume <id>` into each
  recorded pane that is running a bare shell. The dotfiles run it from
  `@resurrect-hook-post-restore-all`.

```sh
ln -sf "$PWD/agent-ping/agent-ping" ~/.local/bin/agent-ping
agent-ping/test.sh          # scratch tmux server (-L agent-ping-test); never touches yours
```

`~/.claude/settings.json`, merged into the existing `hooks`:

```json
"PreToolUse": [{"matcher": "AskUserQuestion",
  "hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" claude"}]}],
"Notification": [{"matcher": "permission_prompt|elicitation_dialog|idle_prompt",
  "hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" claude"}]}],
"Stop": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" claude"}]}],
"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" claude"}]}],
"SessionEnd": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" claude"}]}]
```

`~/.codex/hooks.json`, merged into the existing `hooks` (Codex asks once to trust new hooks):

```json
"PermissionRequest": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" codex", "timeout": 5}]}],
"Stop": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" codex", "timeout": 5}]}],
"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" codex", "timeout": 5}]}],
"SessionEnd": [{"hooks": [{"type": "command", "command": "\"$HOME/.local/bin/agent-ping\" codex", "timeout": 5}]}]
```

Codex's `SessionEnd` always says reason `other`, so any Codex quit drops its row; a Codex session
that Codex itself ends during a shutdown won't be resumed.
