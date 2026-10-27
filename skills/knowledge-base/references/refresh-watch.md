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
  - `git:<url>`: branches and tags by commit hash. On a force-push or deleted branch the old head is pinned as `refs/kb/<sha>` in the mirror ("old head pinned"), so the rewritten history stays readable there;
  - `github-user:<login>`: new repos and pushes;
  - `github-releases:<owner/repo>`;
  - `page:<url>`;
  - `feed:<url>`.
- **Add a target:** `kb watch add <id> git:https://github.com/owner/repo`.
- **Run on a schedule.** No agent is involved: `kb watch run --due --push` checks what's due, commits only the record files that changed (`kb watch: N change(s) in …`) and pushes. If the remote moved, it rebases once and retries; on a conflict the commit stays local and the run fails. Quiet runs touch no files: check times live in the index. `contrib/` in the llms repo has a systemd timer that does this hourly for every registered shared vault.
  Each change is appended to the record's `## Timeline` (newest first), stored as an event, and sent through the profile's `alerts` (ntfy, a webhook, or a command; immediate or digest).
- **Inspect:** `kb watch events --since 2026-10-01`.
- **Freeze a repo as it is now** (before an event, say): `kb watch snapshot <id>` saves `git archive` of HEAD as an immutable original.

**Your optional part:**
1. Read `kb watch events` and the diffs (the mirrors live in the clones root, `~/.local/share/kb/clones/<host>/<owner>/<repo>.git`).
2. Write a short "what changed" note linking the records. For example, "team X pushed a new module to repo Y that adds Z", with the commit range.
3. Promote anything important into evidence or a source capture.

## Reviewing past watch changes

Watches only record *that* something changed. To interpret everything since the last review:

1. Find the newest topic tagged or titled as a watch review and read its `reviewed_through` (none: start from the beginning).
2. `kb watch events --since <reviewed_through> --json`, then group by record.
3. Per repo event, read what changed in the mirror (`~/.local/share/kb/clones/<host>/<owner>/<repo>.git`): `git log --stat <old>..<new>`, then `git log -p` for anything that looks relevant. For releases, read the release notes. Page events can't be reviewed after the fact (only a hash is kept); recapture the page if it matters.
4. Judge relevance against what the vault is for (README.md) and the entity's notes. Promote what matters: a fact on the entity (cite the commit range), a new capture (`kb capture <url>`), an evidence item, or an output.
5. Write a topic `watch-review-<date>` with `reviewed_through: <time of the last event covered>`: per entity, what changed, whether it matters and why, with commit ranges. Say "routine" plainly for noise.

## Snapshots of the whole vault

`kb snapshot <name> -m "morning of the event"` tags the vault's git repo as `kb/<name>`. `--all` tags every registered vault you may access. `git checkout kb/<name>` later shows exactly what was known then.
