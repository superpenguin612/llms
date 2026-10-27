# Front matter and profile

## Rules

- Flat YAML, so Obsidian Properties and Bases can filter it. The only nested key is `originals`.
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
| `superseded_by`, `aliases`, `tags`, `updated` | as needed (`updated` on internal records) |
| scope fields | as the profile's `scope_fields` declare (e.g. `season`, `event`) |

## By trait

| Trait | Keys |
|---|---|
| `origin: external` | `tier` (required if the profile has tiers), `author`, `url`, `given_url`, `issued`, `accessed`, `source_note` (one line: access, currency, bias, expertise), `same_work` / `derived_from` (for independence: copies count once) |
| `origin: internal` | `contributor` or `author`; never `tier` |
| `original: immutable` | `originals: [{path, sha256, bytes, format, captured, fetched_url, http_status, via, obtained_from}]`; when it's empty, `originals_note` says why |
| `text` | `text`, `text_sha256`, `extracted_with`, `text_quality` (`native` · `ocr` · `captions` · `asr-auto` · `manual` · `mixed`) |
| `tracked: refresh` | `refresh` (`never` · `1d` · `weekly` · `monthly` · `before-event` …), `last_checked` |
| `tracked: watch` | `watch: ["git:<url>", "github-user:<login>", "github-releases:<owner/repo>", "page:<url>", "feed:<url>"]`, `watch_every`, `last_checked` |
| image originals | `file: "[[<id>.png]]"` (the cover for Obsidian card views) |
| any | `injection_flag`, `injection_note` (set by tools) |

Types add their own keys in `kb.yaml` (`keys:`, with specs `string` · `date` · `list` · `link` · `links` · `bool` · `int` · `[enum, values]`) and list required ones in `required:`.

## Profile (`kb.yaml`) top-level keys

`schema` (4), `name`, `description`, `access` (`shared` · `local`), `tiers`, `scope_fields`, `tags` (optional controlled list), `phases`, `contexts`, `events` (with `date`; these drive `before-event`), `types`, `inbox.max_age`, `caps.warn_mb` / `caps.max_mb`, `clone_root` (where repo clones and watch mirrors live), `alerts` (`ntfy`, `webhook_env`, `command`, `mode: immediate|digest`, `digest_every`), `collectors` (e.g. `github.token_env`), `secrets.extra_patterns` (extra regexes treated as secrets), `sessions.exclude`.
