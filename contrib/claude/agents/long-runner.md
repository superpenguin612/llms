---
name: long-runner
description: General-purpose agent with a 1-hour prompt cache, for work that waits on commands taking over 5 minutes (test suites, builds, long scripts). The default 5-minute subagent cache goes cold during those waits and the whole context is written again; 1-hour writes cost 2x input instead of 1.25x, so use this only when such waits are expected.
experimental:
  cacheTtl: 1h
---

You are a general-purpose agent. Do the task in the prompt, then report back briefly.
