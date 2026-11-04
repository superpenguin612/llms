---
name: design-walkthrough
description: Walk the user through a design doc you just wrote, before building anything. Use after writing or substantially revising a design doc, when the design rules call for a walkthrough, or when the user asks to go over a design.
---

The user should come away knowing what will be built and which parts were your calls, without
answering a question per item. Post one message, then wait.

## The message

1. **What gets built.** A few short paragraphs of plain prose: the pieces, how they fit together,
   and how it behaves for the user day to day. Not a restatement of the decisions table.
2. **My calls.** Each detail you settled yourself rather than the user (thresholds, names, file
   locations, defaults, ordering, what's left out), one line each, with the alternative you passed
   over. Skip anything the user already decided. If there are none, say so.
3. **Hard to change later.** Things that get costly to undo once built: data formats, public
   names, migrations, anything other tools or people will depend on.
4. **Not verified yet.** Facts the design assumes that you haven't confirmed.
5. The doc's path, and one line: "Reply with anything to change, or go."

No numbered questions, no recommended answers, no "is this what you meant?" per item.

## After the reply

- **"go" (or no changes):** start building.
- **Changes:** update the doc. If they were material, post a shorter message covering only what
  changed and what it affects, then wait again. Small fixes: say what you changed in one line and
  start building.
