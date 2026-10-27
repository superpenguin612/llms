# Refresh, watch, alerts, snapshots

## Refresh: slow and thorough

- **What:** records whose type has `tracked: refresh`, at their `refresh` cadence (`never`, `weekly`, `monthly`, `before-event` …).
- **Command:** `kb refresh --due` (or `kb refresh <id>…`).
- **What it does:** recaptures into a new dated folder and diffs the text. On a change, it moves the old text to `text/history/`, appends a `## Changes` entry, and marks excerpts missing from the new text with `Gone: <date>`. Those excerpts stay valid against the older text.

**Your part** (`kb refresh --due --json` gives the change list):
1. For each changed record, replace the generated `## Changes` line with a one-line summary of what actually changed (a section rewritten, numbers updated, the page removed).
2. Check the Key facts, and the notes citing the record, against the new text. Where they now contradict it, mark them `[contradicted: …]` and tell the user. Don't silently rewrite conclusions.
3. Mine anything new into new excerpts (numbering continues).

## Watch: fast and cheap

- **What:** records whose type has `tracked: watch` list targets in `watch:`:
  - `git:<url>`: branches and tags by commit hash;
  - `github-user:<login>`: new repos and pushes;
  - `github-releases:<owner/repo>`;
  - `page:<url>`;
  - `feed:<url>`.
- **Add a target:** `kb watch add <id> git:https://github.com/owner/repo`.
- **Run on a schedule from cron or a systemd timer.** No agent is involved:
  `*/15 * * * * cd /path/to/vault && kb watch run --due`.
  Each change is appended to the record's `## Timeline` (newest first), stored as an event, and sent through the profile's `alerts` (ntfy, a webhook, or a command; immediate or digest).
- **Inspect:** `kb watch events --since 2026-10-01`.
- **Freeze a repo as it is now** (before an event, say): `kb watch snapshot <id>` saves `git archive` of HEAD as an immutable original.

**Your optional part:**
1. Read `kb watch events` and the diffs (the mirrors live under `clone_root`).
2. Write a short "what changed" note linking the records. For example, "team X pushed a new module to repo Y that adds Z", with the commit range.
3. Promote anything important into evidence or a source capture.

## Snapshots of the whole vault

`kb snapshot <name> -m "morning of the event"` tags the vault's git repo as `kb/<name>`. `--all` tags every registered vault you may access. `git checkout kb/<name>` later shows exactly what was known then.
