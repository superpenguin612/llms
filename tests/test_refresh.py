import contextlib
import io
import json
import os
from unittest import skipUnless

from kb import cli, refresh
from kb.records import parse_excerpts
from kb.util import KBError, today
from kb.vault import open_vault
from tests.helpers import VaultTestCase, make_vault
from tests.test_capture import FakeResp, article, web
from tests.test_extract import HAS

URL = "https://example.com/rules"


@skipUnless(HAS["trafilatura"], "trafilatura not installed")
class TestRefresh(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_vault(self.tmp / "kb")
        self.v = open_vault(start=self.root)
        from kb import capture
        with web({URL: FakeResp(article("<p>Rule nine says the old thing about scoring.</p>"), URL)}):
            rec = capture.capture_url(self.v, URL, id="rules")
        rec.fm["refresh"] = "weekly"
        rec.fm["last_checked"] = "2020-01-01"
        rec.body = rec.body.replace("## Excerpts\n", "## Excerpts\n### e1\n> Rule nine says the old thing\n\n")
        rec.save()
        self.v.reload()

    def run_cli(self, *argv):
        out, cwd = io.StringIO(), os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(["refresh", *argv])
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def test_due_changed(self):
        self.assertEqual([r.id for r in refresh.due_records(self.v, due=True)], ["rules"])
        with web({URL: FakeResp(article("<p>Rule nine now says something new.</p>"), URL)}):
            code, out = self.run_cli("--due", "--json")
        self.assertEqual(code, 0)
        [res] = json.loads(out)
        self.assertEqual((res["id"], res["status"], res["gone"]), ("rules", "changed", ["e1"]))
        rec = open_vault(start=self.root).require("rules")
        self.assertEqual(str(rec.fm["last_checked"]), today())
        self.assertEqual(parse_excerpts(rec.body)[0].gone, today())
        self.assertTrue((self.root / "text" / "history" / "rules" / f"{today()}.md").exists())
        self.assertIn("refresh | --due | 1 checked, 1 changed or failed", (self.root / "log.md").read_text())
        # now it is not due any more
        self.assertEqual(refresh.due_records(open_vault(start=self.root), due=True), [])

    def test_unchanged_bumps_last_checked(self):
        with web({URL: FakeResp(article("<p>Rule nine says the old thing about scoring.</p>"), URL)}):
            code, out = self.run_cli("rules")
        self.assertIn("rules: unchanged", out)
        rec = open_vault(start=self.root).require("rules")
        self.assertEqual(str(rec.fm["last_checked"]), today())
        self.assertNotIn("## Changes", rec.body)

    def test_failure_marks_stale(self):
        with web({URL: 500, "https://archive.org/*": FakeResp('{"archived_snapshots": {}}', "x"),
                  "https://web.archive.org/*": 404}):
            code, out = self.run_cli("rules")
        self.assertIn("rules: failed", out)
        rec = open_vault(start=self.root).require("rules")
        self.assertEqual(rec.fm["status"], "stale")
        self.assertIn("recapture failed", rec.body)

    def test_needs_ids_or_due(self):
        with self.assertRaises(KBError):
            refresh.due_records(self.v)
        rec = self.v.require("rules")
        rec.fm["refresh"] = "never"
        rec.save()
        self.v.reload()
        self.assertEqual(refresh.due_records(self.v, due=True), [])
