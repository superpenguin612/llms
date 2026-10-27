# Lint and deep checks

`kb lint` exits non-zero on errors. Run it before every commit, ideally as a pre-commit hook:

```sh
printf '#!/bin/sh\ncd "$(git rev-parse --show-toplevel)/knowledge-base" 2>/dev/null || cd "$(git rev-parse --show-toplevel)"\nexec kb lint\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**Errors:**
- **Schema and records:**
  - invalid front matter;
  - an unknown type;
  - a missing required key;
  - a bad enum or tier;
  - an id that doesn't match its filename, or a duplicate id or alias.
- **Links and quotes:**
  - an unresolved wikilink or `#eN`;
  - an excerpt not found verbatim in its text.
- **Originals:** missing, a hash mismatch, a capture folder without a valid `capture.yaml`, a file not listed in its folder's manifest, an old-format `originals:` list (run `kb migrate originals`).
- **Origin:** an internal record with a `tier`, or an external one without one.
- **Vault-level:**
  - a `CLAUDE.md` in the vault;
  - a link from a shared vault into a local one;
  - an intake row without a disposition.

**Warnings:**
- loose excerpt matches (normally a whitespace or punctuation difference; fix the quote);
- long descriptions;
- stale inbox files;
- large files;
- cited-claims records that link to nothing;
- overdue refresh;
- style: common AI-writing tells in our own prose (quotes, code and generated blocks are skipped). Rewrite the flagged line; see writing.md.
- a repo clone missing on this machine (restore with `kb capture <url>`), or a leftover `clone_root` profile key.

Lint and build don't write to `log.md`.

**Fixing:**
- **"excerpt not found":** copy the passage again from `kb show <id> --text`. Never edit the text file to make a quote match.
- **"unresolved link":** fix the id, add an `aliases` entry on the target, or create the missing record.
- **"hash mismatch":** an original was modified. Restore it from git; originals are never edited.

`kb lint --fixity` re-hashes every original. `kb lint --deep` prints the judgment checks for you to run:
- Key facts not supported by their excerpts;
- contradictions between records;
- conclusions resting on copies of a single source;
- notes older than the sources they cite;
- people entities without a valid `osint_scope`.

Report what you find. Never auto-fix judgment issues.
