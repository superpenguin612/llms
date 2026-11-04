#!/bin/sh
# Claude Code SessionEnd: record the session in agent-vault with kb (sessions that touched a
# local-only vault are skipped by kb), then commit and push just those files.
# Claude Code kills SessionEnd hooks after ~1.5s and ingest + push takes longer, so the work runs
# detached; flock keeps two sessions ending together from racing on git.
t=$(jq -r '.transcript_path // empty')
[ -f "$t" ] || exit 0
setsid flock "$HOME/.cache/agent-vault-session-end.lock" sh -c '
cd "$HOME/src/agents/agent-vault" || exit 0
kb sessions ingest --tool claude --path "$1" >/dev/null 2>&1 || exit 0
git add -A sessions text log.md
git diff --cached --quiet && exit 0
git commit -qm "sessions: ingest $(basename "$1" .jsonl)" -- sessions text log.md || exit 0
git pull -q --rebase --autostash && git push -q
' sh "$t" </dev/null >/dev/null 2>&1 &
exit 0
