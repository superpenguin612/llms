#!/bin/sh
# Claude Code PreToolUse (Bash): block ways around the global git hooks (global/repos.yaml).
# shortcut: misses `git commit -n` and editing the hooks themselves; it stops slips, not intent.
cmd=$(jq -r '.tool_input.command // ""')
if printf '%s' "$cmd" | grep -Eq -- '--no-verify|core\.hooksPath'; then
  echo "Blocked: this bypasses the repo push policy (agent-vault global/repos.yaml; hooks in llms contrib/git-hooks). Ask the user." >&2
  exit 2
fi
