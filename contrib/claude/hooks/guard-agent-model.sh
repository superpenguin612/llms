#!/bin/sh
# Claude Code PreToolUse (Agent): subagents run on Haiku at xhigh, Sonnet or Opus (global AGENTS.md, "Which model does what", "Delegate procedure").
# A spawn that needs another model says why in its prompt: "[not-sonnet: <reason>]".
# shortcut: doesn't see agents spawned inside Workflow scripts; add a check there if those get used.
in=$(cat)
prompt=$(printf '%s' "$in" | jq -r '.tool_input.prompt // ""')
# Forks copy the parent's whole context and always run on its model, so they're for small, context-heavy asks only.
if [ "$(printf '%s' "$in" | jq -r '.tool_input.subagent_type // ""')" = fork ]; then
  printf '%s' "$prompt" | grep -q '\[fork: [^]]' && exit 0
  echo "Blocked: don't fork for bulk work; spawn a fresh agent with a written brief (global AGENTS.md). If this needs the full context, put \"[fork: <reason>]\" in its prompt." >&2
  exit 2
fi
model=$(printf '%s' "$in" | jq -r '.tool_input.model // ""')
effort=$(printf '%s' "$in" | jq -r '.tool_input.effort // ""')
if [ "$model" = haiku ] && [ "$effort" = xhigh ]; then exit 0; fi
if [ "$model" = sonnet ] || [ "$model" = opus ]; then exit 0; fi
if printf '%s' "$prompt" | grep -q '\[not-sonnet: [^]]'; then exit 0; fi
echo "Blocked: spawn subagents with model \"haiku\" and effort \"xhigh\", or model \"sonnet\" or \"opus\" (global AGENTS.md). If this one needs another model, put \"[not-sonnet: <reason>]\" in its prompt." >&2
exit 2
