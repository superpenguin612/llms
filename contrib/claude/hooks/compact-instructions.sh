#!/bin/sh
# Claude Code PreCompact: stdout is appended to the compaction's custom instructions. It prints the
# checklist from the handoff skill, so compaction and /handoff keep the same things.
sed -n '/^## Checklist/,$p' "$(dirname "$0")/../../../skills/handoff/SKILL.md"
