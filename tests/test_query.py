import contextlib
import io
import json

from kb.cli import main
from tests.fixtures_kb import cd, sample_vault
import os

from tests.helpers import VaultTestCase, make_vault


class TestQuery(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.root = sample_vault(self.tmp / "v", records={
            "topics/flagged.md": ({"type": "topic", "title": "Flagged", "injection_flag": True,
                                  "injection_note": "ignore previous instructions"}, "\nbody text\n")})
        cd(self, self.root)

    def run_kb(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(list(argv))
        return rc, out.getvalue()

    def test_find(self):
        rc, out = self.run_kb("find", "--type", "source")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "fox-page · source · Fox page — A page about a fox.")
        _, out = self.run_kb("find", "--origin", "internal", "--json")
        self.assertEqual(sorted(r["id"] for r in json.loads(out)), ["flagged", "foxes"])
        _, out = self.run_kb("find", "--where", "kind=webpage", "--tag", "animals")
        self.assertIn("fox-page", out)
        _, out = self.run_kb("find", "--where", "kind=pdf")
        self.assertIn("no matching", out)
        self.assertEqual(self.run_kb("find", "--where", "bad")[0], 2)

    def test_grep(self):
        _, out = self.run_kb("grep", "brown", "--in", "excerpt")
        self.assertTrue(out.startswith("fox-page#e1 · "), out)
        _, out = self.run_kb("grep", "foxes", "--in", "text", "--json")
        self.assertEqual(json.loads(out)[0]["ref"], "text/fox-page.md")
        self.assertEqual(self.run_kb("grep", "fox-page AND (")[0], 0)   # bad FTS syntax falls back to terms
        _, out = self.run_kb("grep", r"Second \w+", "--regex", "--in", "text")
        self.assertIn("text/fox-page.md:3 · Second paragraph", out)

    def test_head_and_show(self):
        _, out = self.run_kb("head", "fox-page")
        self.assertNotIn("## Excerpts", out)
        self.assertIn("[1 excerpts", out)
        _, out = self.run_kb("show", "fox-page#e1")
        self.assertIn('<untrusted-source id="fox-page">\n> The quick brown fox jumps\nLocation: p. 1\n</untrusted-source>', out)
        _, out = self.run_kb("show", "foxes")
        self.assertNotIn("untrusted-source", out)
        _, out = self.run_kb("show", "flagged")
        self.assertTrue(out.startswith("WARNING: flagged is flagged"), out)
        self.assertIn('<untrusted-source id="flagged">', out)
        _, out = self.run_kb("show", "fox-page", "--text")
        self.assertIn("Second paragraph", out)
        self.assertEqual(self.run_kb("show", "fox-page#e7")[0], 2)
        self.assertEqual(self.run_kb("show", "foxes", "--text")[0], 2)

    def test_query(self):
        _, out = self.run_kb("query", "SELECT id, type FROM records ORDER BY id", "--json")
        self.assertEqual(json.loads(out)[0], {"id": "flagged", "type": "topic"})
        _, out = self.run_kb("query", "SELECT count(*) AS n FROM excerpts")
        self.assertIn("(1 rows)", out)
        self.assertEqual(self.run_kb("query", "DELETE FROM records")[0], 2)


class TestExactAndRegex(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_vault(self.tmp / "v", records={
            "sources/guide.md": ({"type": "source", "title": "Guide", "kind": "pdf", "tier": "primary",
                                  "originals": [], "originals_note": "test"},
                                 "\n# Guide\n\n## Summary\nRegistry at HKLM\\SOFTWARE\\X.\n\n## Excerpts\n### e1\n"
                                 "> Host 192.0.2.10 runs server schannel = yes here.\nLocation: p. 1\n")},
            files={"text/guide.md": "---\nid: guide\n---\n[p1]\nHost 192.0.2.10 runs server schannel = yes here.\n",
                   "AGENTS.md": "server schannel = yes\n"})

    def run_grep(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["grep", "--json", *argv]), 0)
        return sorted(r["ref"] for r in json.loads(buf.getvalue()))

    def test_exact_maps_to_citations(self):
        os.chdir(self.root)
        self.assertEqual(self.run_grep("--exact", "server schannel = yes"), ["guide#e1", "text/guide.md:5"])
        self.assertEqual(self.run_grep("--exact", "HKLM\\SOFTWARE", "--in", "body"), ["guide:13"])

    def test_regex_with_and_without_ripgrep(self):
        from unittest import mock
        os.chdir(self.root)
        want = self.run_grep("--regex", r"\b\d+\.\d+\.\d+\.\d+\b")
        self.assertEqual(want, ["guide#e1", "text/guide.md:5"])
        with mock.patch("kb.query.shutil.which", return_value=None):
            self.assertEqual(self.run_grep("--regex", r"\b\d+\.\d+\.\d+\.\d+\b"), want)
