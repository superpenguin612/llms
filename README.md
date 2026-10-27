# llms

Tools for working with LLM agents. Currently: **`kb`**, a knowledge-base CLI plus an agent skill. A vault is plain Markdown that agents can query and people can browse in Obsidian.

## kb in one paragraph

A **vault** is a folder (usually in a git repo) with a `kb.yaml` profile. Records are Markdown files with flat YAML front matter, linked with wikilinks (`[[id]]`), with citations pointing at numbered verbatim excerpts (`[[id#e3]]`).

The profile defines **types** as combinations of a few **traits**:
- `origin`: external or internal;
- `original`: kept immutable or not;
- `text`: extracted, OCR or none;
- `quotes`: verbatim or none;
- `role`: input, synthesis or output;
- `tracked`: refresh or watch;
- `claims`: cited or free.

So each domain gets its own vocabulary, and the same tooling enforces the same rules everywhere.

`kb` does the mechanical work:
- capture with dated, immutable originals;
- text extraction with location anchors;
- inbox triage with OCR, entity matching and secret detection;
- a SQLite + FTS5 index;
- Obsidian Bases views;
- lint;
- refresh, plus watches with alerts for fast-changing repos, users, pages and feeds;
- git-tag snapshots;
- agent-session ingest.

`kb` never calls a model. The judgment work (reading sources, describing screenshots, deciding relevance) is done by whichever agent you use, following the procedures in [`skills/knowledge-base/`](skills/knowledge-base/SKILL.md). Those procedures work with any agent that can read files and run commands.

## Install

Requires Python ≥ 3.11 and `pyyaml`. Capture extras: `trafilatura`, `pymupdf`, `markitdown`. Optional system tools: `tesseract` (OCR), `git`, `yt-dlp`.

```sh
python3 -m venv ~/.venvs/kb
~/.venvs/kb/bin/pip install "kb[capture] @ git+https://github.com/superpenguin612/llms"
ln -s ~/.venvs/kb/bin/kb ~/.local/bin/kb
kb doctor
```

To work on it from a checkout: `PYTHONPATH=/path/to/llms python3 -m kb …`.

## Quick start

```sh
kb init my-vault --profile generic          # or: competition, agent-vault
cd my-vault
kb capture https://example.com/some-post    # original + text + stub card; then mine it (skill: capture.md)
kb lint && kb build                         # check, then index + Obsidian views
kb find --type source
kb grep "persistence"
kb show some-post#e1
```

- **Drop files into `inbox/<your-name>/`,** then `kb triage scan`, review the proposals, and `kb triage apply <run>`.
- **Watch a repo:** `kb watch add <entity> git:https://github.com/owner/repo`, then run `kb watch run --due` from cron.

## Agent skill

`skills/knowledge-base/` is an Agent Skills folder (`SKILL.md` + `references/`). Link or copy it into your agent's skills directory. Each vault's `AGENTS.md` carries the always-on query rules, which `kb init` and `kb build` write and keep current.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

The tests make no network calls. Tests that need an optional dependency skip when it's missing.

## Vendored skills

Third-party skills are pinned as git submodules in `vendor/` (updates only when the submodule is bumped, so changes can be reviewed first):

| Submodule | What |
|---|---|
| `vendor/superpowers` | obra/superpowers: brainstorming, plans, TDD, systematic debugging, verification, worktrees, code review, writing skills |
| `vendor/ponytail` | smallest-complete-change coding style, plus review/audit/debt skills |
| `vendor/humanizer` | remove AI-writing patterns from prose |
| `vendor/mattpocock-skills` | mattpocock/skills; only `grilling` (hard design-tree interviews) is linked |

`git submodule update --init`, then `contrib/link-skills` links them (and `knowledge-base`) into `~/.codex/skills` and `~/.claude/skills`. In Claude Code, superpowers and ponytail are installed as plugins instead (`claude plugin install …`), because their plugins carry hooks.

