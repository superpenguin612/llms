# Discover: finding new sources on the web

Discover is a procedure, not a `kb` command. You do the searching and judging; `kb queue` does the dedup and bookkeeping.

## Inputs

- The profile's `seeds`, if it has them: queries, sites to sweep, feeds, channels, GitHub orgs and topics, entities to follow.
- **Gaps:**
  - topics resting on a single source;
  - entities whose `last_checked` is old;
  - "Open questions" sections in notes;
  - whatever the user asks about.

## Procedure

1. **Plan queries:** several per round, phrased the way a source would phrase it (event names, tool names, "write-up", "debrief", "talk", "slides", year).
2. **Search the web** with your web search tool. Sweep listed sites (`site:` queries, archive and index pages). Follow citations out of good sources already in the vault. If your environment has no web access, stop and say so.
3. **For each relevant candidate:**
   `kb queue add <url> --title "<title>" --reason "<one line on why it matters>" --query "<the query that found it>"`.
   `kb queue add` dedups against the vault and earlier candidates and rejections.
4. **Record each search,** including searches that found nothing:
   `kb queue log-search --query "<q>" --results <n> --note "<what was rejected and why, briefly>"`.
   This backs up later "we looked, there's nothing on X" claims.
5. **Get decisions.** The user ticks items in `inbox/discover-<date>.md` (or you tick them when the user has said which tiers to take automatically). Reject an item with `kb queue reject <url> --reason …`.
6. **Capture the ticked items:** `kb queue capture`. Then mine each one ([capture.md](capture.md)).

Judge relevance by what the vault is for (its `description` and existing notes), not by how interesting a page is. Prefer primary material (organizers, authors, official pages) over aggregators. When a story appears in several places, capture the original and note the copies with `derived_from`.
