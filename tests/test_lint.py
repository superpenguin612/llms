import os
import time

import yaml

from tests.fixtures_kb import quiet_main
from kb.lint import Linter
from kb.vault import open_vault
from tests.fixtures_kb import cd, sample_vault
from tests.helpers import VaultTestCase


def rules(root, **kw):
    return {(f.severity, f.rule) for f in Linter(open_vault(start=root), **kw).run()}


class TestLint(VaultTestCase):
    def test_clean_vault(self):
        root = sample_vault(self.tmp / "v")
        found = rules(root)
        self.assertFalse({f for f in found if f[0] == "error"}, found)

    def test_errors(self):
        root = sample_vault(self.tmp / "v", records={
            "notes/bad.md": ({"type": "note", "title": "Bad", "tier": "primary", "status": "wip", "id": "other",
                              "aliases": ["fox-page"]},
                             "\n[[missing]] [[fox-page#e9]] [[foxes#Nope]] [[foxes#Detail]]\n"),
            "notes/nokind.md": ({"type": "widget", "title": "W"}, ""),
            "sources/src3.md": ({"type": "source", "title": "S3", "tier": "primary", "originals_note": "n/a"}, ""),
            "sources/src2.md": ({"type": "source", "title": "S2", "kind": "pamphlet", "originals": []},
                                "\n## Excerpts\n### e1\n> not in the text at all\n### e2\n> The quick brown, fox\n"),
        }, files={"notes/broken.md": "---\ntype: [unclosed\n---\n", "text/src2.md": "The quick brown fox.\n", "sub/CLAUDE.md": "x",
                  "intake/2026-10-01-r1.tsv": "file\tsha256\tdisposition\ttarget\na.png\tab\t\t\nb.png\tcd\tevidence\tx\n",
                  "originals/fox-page/2026-10-01/page.html": b"tampered"})
        found = rules(root, fixity=True)
        for rule in ("type", "id", "required", "value", "tier", "link", "anchor", "excerpt", "originals",
                     "fixity", "claude-md", "intake", "duplicate-alias"):
            self.assertIn(("error", rule), found, rule)
        self.assertIn(("warning", "excerpt-loose"), found)
        v = open_vault(start=root)
        self.assertEqual(len(v.load_errors), 1)  # broken.md is reported by the yaml rule
        self.assertIn(("error", "yaml"), found)

    def test_warnings(self):
        old = time.time() - 10 * 86400
        root = sample_vault(self.tmp / "v", overrides={"caps": {"warn_mb": 0.000001}}, records={
            "notes/lonely.md": ({"type": "note", "title": "L", "description": "x" * 201}, "\nno links\n"),
            "entities/team/t1.md": ({"type": "entity", "title": "T", "kind": "team", "watch": ["git:https://example.org/r"],
                                     "last_checked": "2020-01-01"}, "\n[[foxes]]\n"),
        }, files={"inbox/david/old.png": b"x"})
        os.utime(root / "inbox/david/old.png", (old, old))
        found = rules(root)
        for rule in ("description", "uncited", "tracked-due", "inbox-age", "large-file"):
            self.assertIn(("warning", rule), found, rule)

    def test_tags_and_local_links(self):
        local = sample_vault(self.tmp / "private", overrides={"access": "local", "name": "priv"})
        (self.tmp / "registry.yaml").write_text(yaml.safe_dump({"vaults": {"priv": {"path": str(local)}}}))
        root = sample_vault(self.tmp / "v", overrides={"tags": ["plants"]}, records={
            "notes/leak.md": ({"type": "note", "title": "Leak", "sources": ["[[fox-page]]"]},
                              f"\nsee {local}/notes/x.md\n")})
        found = rules(root)
        self.assertIn(("error", "tag"), found)
        self.assertIn(("error", "local-link"), found)

    def test_cli_strict_and_deep(self):
        root = sample_vault(self.tmp / "v")
        cd(self, root)
        self.assertEqual(quiet_main("lint", "--strict")[0], 1)   # no kb:agents block yet
        self.assertEqual(quiet_main("build")[0], 0)
        self.assertEqual(quiet_main("lint", "--strict", "--deep")[0], 0)
        self.assertIn("] lint | example | 0 errors", (root / "log.md").read_text())
