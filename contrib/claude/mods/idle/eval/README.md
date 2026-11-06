# idle compaction eval

Does the idle mod's own compaction (`compaction: "mod"`) keep as much useful context as the
engine's (`compaction: "engine"`)? This probe checks that on real transcripts.

## Run

```sh
python3 contrib/claude/mods/idle/eval/run.py --out ~/idle-eval ~/.claude/projects/<project>/<id>.jsonl
```

`--out` must be outside this git work tree: transcripts and results are private. The script
refuses a directory inside the repo. It needs the `claude` CLI on `PATH`, already signed in,
and Python 3.11+ (stdlib only). The prompts it sends are `probes.md`, `answer.md` and `grade.md`
in this folder.

## What it does

For each transcript it runs two forked `/compact`s, one per mode, from the transcript's own cwd.
Each fork gets a tiny first prompt (so there is something to fork), then `/compact`. The script
reads each new transcript, renders the summary and the messages the fork preserved, generates
about 15 probes from the full original transcript, answers them blind from each compaction, and
has a model grade both answer sets without knowing which mode produced which.

Output goes to `DIR`:

- `<sessionId>/engine.md`, `<sessionId>/mod.md`: what each compaction left;
- `<sessionId>/probes.json`, `answers-engine.json`, `answers-mod.json`, `grades.json`;
- `results.json` and `report.md`.

If `mod` produces the engine's `ran out of context` framing, that transcript is marked
`mode_fallback` and is not scored as the mod.

## What it costs

It is not free: two compaction calls, one probe-generation call, two answer calls and one grading
call per transcript, all on Sonnet. The probe and compaction calls read the whole transcript, so
cost grows with transcript length: each fork writes the whole transcript to the cache once
(~150k tokens for a 150k session). `results.json` records the tokens of each fork's own turn
(`tokens`); ignore its `cost_usd`, which is the resumed session's running total and includes the
original session. The compaction, answer and grading calls are extra (see your Claude usage).

Do not run this in the test suite or on transcripts you are not willing to send to the model.

## What passing means

A transcript is **scored** when both compactions ran and the mod summary used the mod framing.
The mod total is the sum of its 0/1/2 grades over all probes; the engine total is the same for
the engine. A transcript passes when `mod >= engine`. The overall result passes when **every
scored transcript** passes. Fallback transcripts and errors are listed in the report but are not
counted as mod evidence, so check that at least one transcript was scored before trusting a pass.
