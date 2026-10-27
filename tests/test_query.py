import contextlib
import io
import json

from kb.cli import main
from tests.fixtures_kb import cd, sample_vault
from tests.helpers import VaultTestCase


class TestQuery(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.root = sample_vault(self.tmp / "v", records={
            "notes/flagged.md": ({"type": "note", "title": "Flagged", "injection_flag": True,
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
        self.assertEqual(json.loads(out)[0], {"id": "flagged", "type": "note"})
        _, out = self.run_kb("query", "SELECT count(*) AS n FROM excerpts")
        self.assertIn("(1 rows)", out)
        self.assertEqual(self.run_kb("query", "DELETE FROM records")[0], 2)
