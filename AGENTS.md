# llms: how to work in this repo

Public repo. The `kb` knowledge-base CLI (`kb/`), its agent skill
(`skills/knowledge-base/`), starter profiles and templates (`kb/templates/`).

## Rules

- **Public: nothing private.** No personal data, no team or client names, no
  competition material, no real transcripts or secrets, not even in tests.
  Fixtures use made-up or public content only.
- Design docs don't live here. The spec this code implements is kept privately;
  the code, README and skill references must stand on their own.
- Runtime dependencies: Python ≥ 3.11 stdlib + `pyyaml`. Everything else
  (trafilatura, pymupdf, markitdown, tesseract, git, yt-dlp, rg) is optional,
  imported or called lazily, with a one-line install hint when missing.
- `kb` never calls a model, never writes a `CLAUDE.md`, and never rewrites git
  history or commit messages.
- Output: concise by default, `--json` for machines; errors raise `KBError`
  with a message that says how to fix the problem.
- Agent-neutral: skill text names capabilities ("search the web", "view the
  image"), never one agent tool's function names.

## Layout

- Core modules (`util`, `records`, `profile`, `vault`, `secrets`, `ocr`,
  `inject`, `cli`) are shared by every command; change them carefully and keep
  their tests passing.
- One module per command group; each defines `register(subparsers, common)`
  and is listed in `kb/cli.py` `MODULES`.

## Tests

`python3 -m unittest discover -s tests -t .` from the repo root. No network:
mock `subprocess` and `urllib`. Build vaults with `tests/helpers.make_vault`.
