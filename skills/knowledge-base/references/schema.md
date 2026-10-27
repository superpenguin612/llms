# Front matter and profile

## Rules

- Flat YAML only, so Obsidian Properties and Bases can show and filter every key. Per-file capture details live in `capture.yaml` files, not in front matter.
- ISO dates (`2026-10-24`, `2026-10`, `2026`); datetimes in UTC with `Z`.
- **Leave a key out when its value is unknown.** Never write `""` or "unknown".
- Enum keys hold only enum values. Free text goes in `*_note` keys.
- **Tools fill measured facts** (hashes, sizes, capture times, final URLs, git data). **You fill judgments** (`tier`, `description`, relevance, links). Dates reported by extractors are hints; confirm them from the content.

## Every record

| Key | |
|---|---|
| `type` | required: a type from `kb.yaml` |
| `title` | required; same as the H1 |
| `description` | one self-contained sentence, ≤ 200 characters: the subject, its scope, why it matters |
| `status` | `active` (default) · `draft` · `stale` · `superseded` · `implemented` |
| `superseded_by`, `aliases`, `updated` | as needed (`updated` on internal records) |
| `tags` | only tags defined in kb.yaml `tags:` (`name: "meaning"`). Lint rejects anything else. A new tag is a deliberate decision: check `kb tags` first, and add one only when no existing tag fits, with `kb tags add NAME "meaning"` (agents: propose it to the user first). |
| scope fields | as the profile's `scope_fields` declare (e.g. `season: int`, `region: [east, west]`); same specs as type `keys`, checked by lint on every record |

## By trait

| Trait | Keys |
|---|---|
| `origin: external` | `tier` (required if the profile has tiers), `author`, `url`, `given_url`, `issued`, `accessed`, `source_note` (one line: access, currency, bias, expertise), `same_work` / `derived_from` (for independence: copies count once) |
| `origin: internal` | `contributor` or `author`; never `tier` |
| `original: immutable` | `original` (vault path of the latest capture's main file, or the evidence file), `sha256` (of that file), `captured` (UTC with `Z`; for evidence, without `Z` when the timezone is unknown, with `captured_from` saying where the time came from). Evidence also has `bytes` and `format`. With no original, `original_note` says why. Per-file details are in `originals/<id>/<date>/capture.yaml`: `{captured, via, given_url, fetched_url, http_status, obtained_from, main, files: [{name, sha256, bytes, format}]}`. |
| `text` | `text` (a path, or `clone:<host>/<owner>/<repo>@<commit>` for repos), `text_sha256`, `extracted_with`, `text_quality` (`native` · `ocr` · `captions` · `asr-auto` · `manual` · `mixed`) |
| `tracked: refresh` | `refresh` (`never` · `1d` · `weekly` · `monthly` · `before-event` …), `last_checked` |
| `tracked: watch` | `watch: ["git:<url>", "github-user:<login>", "github-releases:<owner/repo>", "page:<url>", "feed:<url>"]`, `watch_every`, `last_checked` |
| image originals | `file: "[[<id>.png]]"` (the cover for Obsidian card views) |
| any | `injection_flag`, `injection_note` (set by tools) |

Types add their own keys in `kb.yaml` (`keys:`, with specs `string` · `date` · `list` · `link` · `links` · `bool` · `int` · `[enum, values]`) and list required ones in `required:`.

## Profile (`kb.yaml`) top-level keys

`schema` (4), `name`, `description`, `access` (`shared` · `local`), `tiers`, `scope_fields`, `tags` (the controlled vocabulary: `name: "meaning"`; `{}` means no tags allowed yet; omit the key to leave tags uncontrolled), `phases`, `contexts`, `events` (with `date`; these drive `before-event`), `types`, `inbox.max_age`, `caps.warn_mb` / `caps.max_mb`, `alerts` (`ntfy`, `webhook_env`, `command`, `mode: immediate|digest`, `digest_every`), `collectors` (e.g. `github.token_env`), `secrets.extra_patterns` (extra regexes treated as secrets), `sessions.exclude`.
