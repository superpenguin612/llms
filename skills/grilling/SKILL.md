---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

<!-- Adapted from mattpocock/skills (MIT, Copyright (c) 2026 Matt Pocock), vendor/mattpocock-skills. -->

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Word each question so "yes" accepts your recommended answer.

**Use a question dialog when your tool has one** (a multiple-choice prompt the user answers by picking options). The user prefers it to typed answers. Dialogs are small (often at most 4 questions per dialog and 4 options per question, with short labels), so:

- Put the context a round needs (facts you found, what changed since the last round) as text before the dialog; the dialog may hide earlier text, so each question must also stand alone.
- Make the recommended option the first one and mark it "(Recommended)". Leave out an "Other" option; the dialog offers free text.
- Use multi-select only when the choices are not mutually exclusive.
- A frontier bigger than one dialog takes several dialogs in a row; it is still one round.
- Fall back to the numbered text format above when there is no dialog, or when a question needs long options (code, lists) that don't fit.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent to find it; don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.
