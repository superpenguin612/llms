"""A small public-content vault shared by the build/lint/query tests."""

from kb.util import sha256_bytes
from tests.helpers import make_vault

ORIGINAL = b"<html><p>The quick brown fox jumps over the lazy dog.</p></html>"
TEXT = "[p1]\nThe quick brown fox jumps over the lazy dog.\nSecond paragraph about foxes.\n"


def sample_vault(root, **kw):
    records = {
        "sources/fox-page.md": ({
            "type": "source", "title": "Fox page", "description": "A page about a fox.", "kind": "webpage",
            "tier": "primary", "url": "https://example.org/fox", "tags": ["animals"],
            "originals": [{"path": "originals/fox-page/2026-10-01/page.html", "sha256": sha256_bytes(ORIGINAL)}],
            "last_checked": "2026-10-01"},
            "\n# Fox page\n\n## Summary\nFoxes.\n\n## Excerpts\n### e1\n> The quick brown fox jumps\nLocation: p. 1\n"),
        "notes/foxes.md": ({"type": "note", "title": "Foxes", "description": "What we know about foxes.",
                            "sources": ["[[fox-page]]"]},
                           "\n# Foxes\n\nFoxes jump ([[fox-page#e1]]).\n\n## Detail\nMore.\n"),
    }
    records.update(kw.pop("records", {}))
    files = {"originals/fox-page/2026-10-01/page.html": ORIGINAL, "text/fox-page.md": TEXT}
    files.update(kw.pop("files", {}))
    return make_vault(root, records=records, files=files, **kw)


def cd(test, path):
    """chdir for one test, restored afterwards."""
    import os
    old = os.getcwd()
    os.chdir(path)
    test.addCleanup(os.chdir, old)


def quiet_main(*argv):
    """Run the CLI with stdout captured; returns (exit code, output)."""
    import contextlib
    import io
    from kb.cli import main
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(list(argv))
    return rc, out.getvalue()
