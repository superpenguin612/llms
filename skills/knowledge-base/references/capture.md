# Capturing and mining a source

## Capture

```sh
kb capture https://example.com/post          # web page (raw HTML + headers, text via trafilatura)
kb capture ./report.pdf --kind pdf           # local file (PDF, Office, image, subtitles, text, archive)
kb capture ./other.pdf --id existing-id --recapture   # same work, new file: add a capture (without --recapture a different file under an existing id is refused)
kb capture https://github.com/owner/repo     # repo: clone into the clones root, README + tree + commit
kb capture https://github.com/owner/repo --ref dev   # repo at a branch, tag, pull/N/head or full sha: its own card
kb capture https://youtu.be/…                # video: info + subtitles, no media
```

- Every capture folder `originals/<id>/<date>/` gets a `capture.yaml` that kb writes: the capture time, tool, fetched URL, HTTP status, the main file, and every file's sha256, size and format. Web captures keep the server's `headers.txt`.
- **Repos** also get a linked project entity (see model.md). Watching is opt-in: `kb capture <repo-url> --watch` adds a `git:` watch target, or add one later with `kb watch add <entity> git:<url>`.
- **Repo clones** live outside vaults, in one standard place: `$KB_CLONES`, else `$XDG_DATA_HOME/kb/clones`, else `~/.local/share/kb/clones`. Each is at `<host>/<owner>/<repo>`. Cards record the portable `text: clone:github.com/owner/repo@<commit>`. On another machine, `kb capture <url>` restores a missing clone without making a new capture, re-fetching the commits its excerpts quote (it warns about any the remote no longer serves).
- **Pinned commits** are kept as `refs/kb/<sha>` in the clone, so gc never drops them even after a force-push or deletion upstream. `--ref` captures content that isn't on the default branch (another branch, a tag, a PR head, a commit only reachable by sha) as a separate card, id `<repo card id>-<ref>`, url `https://<host>/<owner>/<repo>/tree/<sha>`; nothing is checked out.
- `kb capture` dedups by URL and content hash. If a match exists, it adds a new dated capture to the existing record instead of creating a new one.
- It saves the original, writes `text/<id>.md` with anchors, and creates a stub card with tool metadata filled in.
- **If capture fails** (paywall, login, JS-only page), keep what you got, set `depth: partial`, and explain in `original_note`. A dead page falls back to an existing Wayback snapshot. **Never submit pages to archive services.**

## Mine (your part)

1. **Read the whole text** (`kb show <id> --text`). If you read only part of it, set `depth: partial` and give the gaps in `depth_note`.
2. **Fill the judgment front matter:**
   - `title`, `description`;
   - `tier`, from the profile's list: the source's *role*, not a trust score;
   - `source_note`;
   - `author`, `issued` (confirmed from the content);
   - scope fields, `tags`, `entities` (link the author's or org's entity record, creating a stub when missing);
   - `same_work` / `derived_from` when this is a copy of something already in the vault;
   - `depth`, `mined_by` (your model/agent name), `mined_on`.
3. **Write the body** following [writing.md](writing.md) (what goes in each part, format, an example):
   - `## Summary`: 2-5 self-contained sentences. Name the subject and its scope (who, where, when) and what the source is most useful for. No pronouns that need context.
   - `## Key facts`: atomic, self-contained claims. Each one names its subject, year and scope and ends with the excerpt(s) that support it: `- In 2025 the qualifier awarded 40% of points to injects. [[id#e2]]`. Use the markers `[inferred: basis]`, `[estimate]`, `[secondhand]`, `[unverified]` and `[contradicted: [[other#e4]]]`.
   - `## Excerpts`: generous, verbatim, numbered `### e1`, `### e2` … in source order. Each is a `>` quote followed by `Location: p. 12 | slide 7 | 00:31:05 | §"Heading" ¶2 | path/file.go L40-52 @ abc1234`. Copy the text exactly; `[...]` marks a gap. Append only, never renumber.
   - `## Notes`: what you skipped and why, errors in the source, contradictions with other records (link both), and what the source does **not** cover, so nobody re-reads it looking for that.
### Quoting rules

- **One excerpt is one contiguous passage:** a single block of consecutive `>` lines. Two separate passages become two excerpts. Within the passage, `[...]` marks a gap, and a gap may span lines.
- **Copy from `kb show <id> --text`, not from the original's rendering.** Matching ignores whitespace, line breaks, Markdown emphasis, link syntax (`[text](url)` matches `text`) and curly vs straight quotes. Words wrapped across lines after `-`, `/` or `\` (common in PDFs) are rejoined, so quote them as the source prints them: `AES-CFB8`.
- **Keep the source's own typos.** Note them in `## Notes`.
- **Literal brackets in the text** (some extractors wrap paragraphs in `[...]`): quote the inner text, so they can't be confused with a gap marker.
- **Location formats:**

  | Source | Format |
  |---|---|
  | PDF | `p. 12` (or `pp. 12-13`) |
  | slides | `slide 7` |
  | video / audio | `00:31:05` |
  | web page with headings | `§"Heading" ¶3`, counting paragraphs and list items under that heading; `§"Heading" list item 2`, `§"Heading" table, row "X"`; text before the first heading is `§top` |
  | repo files | `path/to/file L40-52 @ abc1234`, or `L7 @ abc1234` for one line. Spaces in the path are fine (`windows/Domain Controller/x.ps1 L10-20 @ abc1234`). Lint checks the quote against `git show abc1234:path/to/file` |
  | JSON / code captures | `L20 $.vulnerabilities[0].cve.id`: the line in the extracted text's code block plus a JSON path |

### Judgment calls

- **Dates.** Extractor dates in the stub are hints.
  - Confirm `issued` from the text or the original (HTML metadata counts; say so in `source_note`).
  - If nothing confirms it, delete the key; never leave a wrong date in place.
  - A visible "updated" date goes in `revised`.
- **Author.** People as written. For an organization's page, the organization. For a repo, the owner (user or org) unless the README names an author.
- **Depth.**
  - `full` means everything with substance was read; boilerplate can be skipped if Notes says so.
  - For a repo, reading the README and the main scripts but skimming helpers is `partial`, with `depth_note` saying what was skipped.
- **Markers.** `[contradicted: [[other#e4]]]` holds only the link; explain in Notes. A summary-style fact supported by one representative excerpt gets `[inferred: …]`.
- **The stub's Notes line** ("Stub written by kb capture…"): replace it with real notes.
- **Entity links.** If an entity you link doesn't exist, create a stub (type, title, description, kind), unless the task says not to. In that case, list the missing ids in your report.

4. **Use it.** Cite it from the notes, entities and outputs it informs. A source that turned out empty still keeps its card (`depth: empty`).
5. Run `kb lint` and fix every error. Most excerpt errors mean a quote isn't verbatim.
