# Writing records

Records are read by people in Obsidian and by agents through `kb`. Write for a busy reader who knows the domain but hasn't read the source: plain, specific, short. Numbers, names and dates beat adjectives. `kb lint` flags common AI-writing tells (overused words, "This source describes…", trailing "-ing" riders, not-X-but-Y, dash chains); fix the lines it flags.

## What goes where

**`description`**: one sentence, at most 200 characters. It's the line every index and search result shows.
- Shape: *who/what, when: what it is or says, and why it matters.*
- Good: `CISA ED 20-04 (Sept 2020, now closed): ordered federal agencies to patch every Windows DC for Zerologon within 3 days.`
- Bad: `This page provides important information about a critical vulnerability.`

**Source `## Summary`**: 2-4 sentences, in this order:
1. What it is: author and their role, date, form ("Microsoft's deployment guidance, updated 2026-09").
2. What it covers and its scope (versions, years, region, event).
3. What it is most useful for in this vault.
4. Limits or bias, only when they matter ("vendor page; dates reflect 2020 plans").

Start with the subject, never with "This source/article/page…".

**`## Key facts`**: one claim per bullet, most decision-relevant first.
- Each bullet stands alone: name the subject, the version or year, and the scope. No "it", "this event" or "the tool".
- Keep exact values: numbers, versions, event IDs, commands, dates.
- End with the excerpt(s) that support it: `[[id#e3]]`. Mark anything not directly stated: `[inferred: basis]`, `[estimate]`, `[secondhand]`.
- Aim for 5-15. A source with 60 excerpts still gets a short, ranked list; the excerpts hold the rest.

**`## Notes`**: what you skipped and why, errors or typos in the source, contradictions with other records (link both), and what the source doesn't cover.

**Entity `## Summary`**: what it is, why this vault tracks it, and the one thing to know first. Facts below are dated and cited; the Timeline is appended by tools.

**Topics and playbooks**: lead with the answer, then the support.
- Title as the subject or question ("How Zerologon works", "Detecting Zerologon").
- First paragraph: the answer a reader would want if they read nothing else.
- Sections by question or step, not by source.
- Every claim cited. Our own judgments are marked as judgments ("Judgment: treat it as critical, because…").
- Playbooks are steps someone can follow: imperative, numbered, commands in code blocks, each step cited or marked as ours.

**Outputs** (findings, requirements): follow the domain's template or downstream format; keep evidence links in front matter (`supports` on the evidence side) so the generated table stays right.

## Format

- Headings only for the standard sections; no decorative headings or bold-on-every-item.
- Bullets for lists of separate facts; prose for explanation.
- Tables when comparing the same attributes across items (versions, event IDs).
- Code blocks for commands, paths, registry keys and config lines.
- Wikilinks for anything in the vault (`[[id]]`, `[[id#e3]]`); plain URLs only for things outside it.

## Example (rewritten)

Before:
> This advisory provides a comprehensive overview of how Samba is affected by Zerologon, highlighting the crucial role of the server schannel setting. It is not just a Windows issue, but one that impacts Samba as well.

After:
> Samba Team advisory on CVE-2020-1472 (September 2020, by Andrew Bartlett and Douglas Bagnall). Samba is affected only as a domain controller: 4.8 and later are safe by default unless `server schannel` is set to `no` or `auto`, and 4.7 and earlier need `server schannel = yes`. Most useful for deciding whether a Samba DC needs patching or just a config check; fixed releases are 4.10.18, 4.11.13 and 4.12.7.

For a deliberate polish of things people read closely (a README, a topic someone will present), run a dedicated humanizer pass; for everyday mining, this page and `kb lint` are enough.
