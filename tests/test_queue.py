import sys
import types
from unittest import mock

from kb import queue
from kb.util import KBError
from kb.vault import open_vault
from tests.helpers import VaultTestCase, make_vault


class TestQueue(VaultTestCase):
    def setUp(self):
        super().setUp()
        root = make_vault(self.tmp / "v", records={
            "sources/known.md": ({"type": "source", "title": "Known Post", "kind": "webpage",
                                  "url": "https://example.com/known"}, "\n")})
        self.v = open_vault(start=root)

    def test_add_dedup_reject_list(self):
        r = queue.add(self.v, "https://example.org/new", reason="explains scoring", query="scoring rules",
                      title="New [draft] post")
        self.assertTrue(r["added"])
        text = (self.v.root / r["file"]).read_text()
        self.assertTrue(text.startswith("# Discover queue"))
        self.assertIn("- [ ] [New (draft) post](https://example.org/new) — explains scoring (query: scoring rules)", text)
        self.assertFalse(queue.add(self.v, "http://www.example.org/new/")["added"])           # already queued
        self.assertIn("[[known]]", queue.add(self.v, "https://example.com/known")["reason"])   # in the vault
        self.assertFalse(queue.add(self.v, "https://example.net/x", title="known post")["added"])  # by title
        queue.reject(self.v, "https://example.org/new", "vendor marketing")
        self.assertIn("- [-] [New (draft) post](https://example.org/new) — explains scoring (query: scoring rules)"
                      " — rejected: vendor marketing", (self.v.root / r["file"]).read_text())
        self.assertIn("rejected", queue.add(self.v, "https://example.org/new")["reason"])
        queue.reject(self.v, "https://example.net/never", "off topic")   # not queued: recorded anyway
        self.assertFalse(queue.add(self.v, "https://example.net/never")["added"])
        with self.assertRaises(KBError):
            queue.add(self.v, "not a url")

    def test_capture_ticked(self):
        queue.add(self.v, "https://example.org/a", reason="r1")
        path = self.v.root / queue.add(self.v, "https://example.org/b")["file"]
        path.write_text(path.read_text().replace("- [ ] [https://example.org/a]", "- [x] [https://example.org/a]"))
        mod = types.ModuleType("kb.capture")
        mod.capture_url = lambda vault, url, **kw: types.SimpleNamespace(id="src-a")
        with mock.patch.dict(sys.modules, {"kb.capture": mod}):
            r = queue.capture(self.v)
            self.assertEqual(r["captured"], [{"url": "https://example.org/a", "id": "src-a"}])
            self.assertEqual(queue.capture(self.v)["captured"], [])   # already marked
        text = path.read_text()
        self.assertIn("- [x] [https://example.org/a](https://example.org/a) — r1 → [[src-a]]", text)
        self.assertIn("- [ ] [https://example.org/b](https://example.org/b)", text)
        with mock.patch.dict(sys.modules, {"kb.capture": None}):
            with self.assertRaises(KBError):
                queue.capture(self.v)

    def test_cli_list_and_log_search(self):
        from kb.cli import main
        queue.add(self.v, "https://example.org/a")
        with mock.patch("os.getcwd", return_value=str(self.v.root)), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            self.assertEqual(main(["queue", "list"]), 0)
            self.assertEqual(main(["queue", "log-search", "--query", "ctf scoring", "--results", "3",
                                   "--note", "2 queued"]), 0)
        self.assertIn("[pending]", out.getvalue())
        self.assertIn("search | ctf scoring | 3 result(s); 2 queued", (self.v.root / "log.md").read_text())
