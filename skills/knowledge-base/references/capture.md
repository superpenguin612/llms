# Capturing and mining a source

## Capture

```sh
kb capture https://example.com/post          # web page (raw HTML + headers, text via trafilatura)
kb capture ./report.pdf --kind pdf           # local file (PDF, Office, image, subtitles, text)
kb capture https://github.com/owner/repo     # repo: clone into clone_root, README + tree + commit
kb capture https://youtu.be/…                # video: info + subtitles, no media
```

- `kb capture` dedups by URL and content hash. If a match exists, it adds a new dated capture to the existing record instead of creating a new one.
- It saves the original, writes `text/<id>.md` with anchors, and creates a stub card with tool metadata filled in.
- **If capture fails** (paywall, login, JS-only page), keep what you got, set `depth: partial`, and explain in `originals_note`. A dead page falls back to an existing Wayback snapshot. **Never submit pages to archive services.**

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
3. **Write the body:**
   - `## Summary`: 2-5 self-contained sentences. Name the subject and its scope (who, where, when) and what the source is most useful for. No pronouns that need context.
   - `## Key facts`: atomic, self-contained claims. Each one names its subject, year and scope and ends with the excerpt(s) that support it: `- In 2025 the qualifier awarded 40% of points to injects. [[id#e2]]`. Use the markers `[inferred: basis]`, `[estimate]`, `[secondhand]`, `[unverified]` and `[contradicted: [[other#e4]]]`.
   - `## Excerpts`: generous, verbatim, numbered `### e1`, `### e2` … in source order. Each is a `>` quote followed by `Location: p. 12 | slide 7 | 00:31:05 | §"Heading" ¶2 | path/file.go L40-52 @ abc1234`. Copy the text exactly; `[...]` marks a gap. Append only, never renumber.
   - `## Notes`: what you skipped and why, errors in the source, contradictions with other records (link both), and what the source does **not** cover, so nobody re-reads it looking for that.
4. **Use it.** Cite it from the notes, entities and outputs it informs. A source that turned out empty still keeps its card (`depth: empty`).
5. Run `kb lint` and fix every error. Most excerpt errors mean a quote isn't verbatim.
