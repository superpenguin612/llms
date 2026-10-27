# The model: traits, types, layers

## Traits

`kb` knows only traits. A profile (`kb.yaml`) defines **types** as combinations of traits, and every rule in `kb` (folders, required keys, lint checks, indexing) follows from them.

| Trait | Values (default first) | Meaning |
|---|---|---|
| `origin` | `external` · `internal` (required) | Someone else made it, or we did. External records carry a `tier`; internal ones never do. |
| `original` | `none` · `immutable` | The exact bytes are kept under `originals/` (or next to the sidecar, for evidence), hashed, and never edited |
| `text` | `none` · `extracted` · `ocr` | A full-text file `text/<id>.md` with location anchors exists |
| `quotes` | `none` · `verbatim` | The record has numbered excerpts (`### e1` …) that lint checks against the text |
| `role` | `synthesis` · `input` · `output` | Material coming in, our thinking, or something meant to leave the vault |
| `tracked` | `none` · `refresh` · `watch` | Not revisited; recaptured on a slow cadence; checked often for changes |
| `claims` | `free` · `cited` | Whether statements must cite records |

The starter profiles define these default types:

| Type | Traits | Used for |
|---|---|---|
| `source` | external, immutable, extracted text, verbatim quotes, input, refresh | something someone else published |
| `entity` | internal, synthesis, watch, cited | our running notes on a person, team, org, project, host or product |
| `evidence` | internal, immutable, OCR text, input | something we captured: screenshot, log, pcap, scan, ticket |
| `topic` | internal, synthesis, cited | our synthesis on a subject: "what we know about X" |
| `playbook` (competition profile) | internal, synthesis, cited | how we do something: procedures, checklists, commands |
| `output` | internal, output, cited | findings, requirements, anything a downstream process consumes |

The agent-vault profile instead has `design`, `project`, `session`, `topic` and `source`. Any domain can rename, add or remove types.

## Layers

```
originals/<id>/<YYYY-MM-DD>/…   exact bytes + kb-written capture.yaml, one folder per capture, never overwritten
text/<id>.md                    extracted text with anchors: [p12] [slide 7] [00:31:05] [L40]
text/history/<id>/<date>.md     earlier texts, kept when a recapture changes the text
<type folder>/<id>.md           the record: front matter + body
.kb/kb.sqlite                   derived index (gitignored; `kb build` rebuilds it)
views/*.base                    generated Obsidian Bases views (live tables and card galleries)
inbox/<who>/…                   drop zone; triage empties it
intake/<date>-<run>.tsv         what happened to every file that came through the inbox
log.md                          append-only: `## [date] op | ref | detail` (captures, triage, watch, ingest; not lint/build)
~/.local/share/kb/clones/       repo clones and watch mirrors, shared by all vaults ($KB_CLONES overrides)
```

## Ids, links, citations

- The **id is the filename stem**, unique across the vault, and never renamed once linked (use `aliases`).
- **Links:** `[[id]]`, `[[id#e3]]`, `[[id#Heading]]`, `[[id|text]]`, and embeds `![[file.png]]`. Front matter links are quoted strings, e.g. `hosts: ["[[web-01]]"]`.
- **Excerpt numbers are append-only.** A withdrawn excerpt keeps its heading with `Withdrawn: <reason>`. An excerpt missing from a newer capture gets `Gone: <date>` and is still valid against the older text.
- **Never edit** inside `<!-- kb:NAME start -->` … `<!-- kb:NAME end -->` blocks; `kb build` rewrites them.

A vault made by an older kb (nested `originals:` lists, machine-specific clone paths) is converted with `kb migrate originals --dry-run`, then `kb migrate originals`.

## Repos are two records

A captured repo is a pair:
- **The source card** (e.g. `repo-owner-tool`, type `source`, external): what the repo contained **at one commit**. It holds the README, the file list and verbatim excerpts pinned to that commit, valid forever even if the repo changes or disappears. Recapturing adds a dated capture to the same card. A busy repo never creates new cards on its own.
- **The project entity** (e.g. `owner-tool`, kind `project`, internal): the repo **as a living thing**. It holds our facts about it, links to its capture cards, and the `## Timeline` that `kb watch` appends to (new commits, tags, releases, force-pushes).

`kb capture <repo-url>` creates the project entity if it's missing and links it from the card's `entities`. Add `--watch` to also watch the repo; many captured repos are one-off references that don't need it.

