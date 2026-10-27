# Triage: processing the inbox

Everything new enters a vault through `inbox/<contributor>/`: screenshots, terminal logs, zips, PDFs, a teammate's write-up, a text file of URLs. Nobody files things by hand. Triage routes everything once, and afterwards the inbox is empty except for items held with a question.

## 1. Scan

```sh
kb triage scan --context <context-id>     # context = the event or engagement, from kb.yaml contexts
```

The scan hashes, dedups and unpacks. It OCRs images, reads timestamps, extracts IPs, hostnames, CVEs and URLs, matches them against entity handles, and flags secrets. It writes `.kb/triage/<run>/proposals.jsonl`, one proposal per file, and prints a summary.

## 2. Review (your part)

Work through `proposals.jsonl` in batches of about 20. For each proposal:

- **Evidence (images): view the image itself, every time.** Never write a description from OCR text, file names, or a chat transcript alone.
  - `title`: what the image shows, specific: "secretsdump of web-01 domain controller after Zerologon", not "terminal output".
  - `description`: 1-3 lines on what is visible: the tool, the target, the result, any identifying details.
  - If the file came with transcript or chat context, that context can inform `phase` and `supports`. Never copy an assistant's chat reply as the description.
- **`phase`:** from the profile's `phases`. Report-writing, tooling and infrastructure shots get `report` / `infra` and are not linked as support for findings.
- **`links`:** confirm or fix `suggest_links` (hosts and entities seen in the image), and add any the regexes missed.
- **`supports`:** the output records this evidence backs (e.g. a finding). If the evidence clearly shows a new issue with no output yet, create the output record first (slug id, no numbers), then link it.
- **`sensitive`:** confirm the detector's flags (`secrets`, `pii`, `client-data`) and add any it missed. Don't redact here; downstream skills make redacted copies.
- **Near-duplicates:** keep the most informative shot as the primary and set the others' route to `duplicate` (or keep them and link them with `same_as`).
- **Write-ups from teammates** (route `note`): keep the file as it is. Afterwards, split its content into the right outputs, entities and notes, and cite it.
- **Published documents** (route `source`) and **URL lists** (route `capture`) are captured during apply; mine them afterwards ([capture.md](capture.md)).
- **Anything unclear:** set `route: hold` with a `question`, and ask the user. Don't guess.

## 3. Apply

```sh
kb triage apply <run>
kb lint && kb build
```

Apply moves the files, writes the sidecars and the intake manifest, and empties the inbox. It refuses evidence proposals without a title and description. It is safe to re-run after an interruption.

## During a live event

- Each person drops files into their own `inbox/<name>/` and pushes whenever convenient.
- **One triage owner** scans, reviews, applies and pushes on a cadence (e.g. hourly). That owner is the only writer to evidence, host entities and outputs, which avoids conflicting edits.
- Create outputs as soon as evidence shows an issue. Merging two outputs later means setting `status: superseded` and `superseded_by` on one; nothing is renumbered.
- `kb triage status` shows the pending runs and the held questions.
