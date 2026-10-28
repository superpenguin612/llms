import yaml

from kb.lint import Linter
from kb import capture
from kb.migrate import migrate_originals, reextract
from kb.records import read_record
from kb.util import sha256_bytes
from kb.vault import open_vault
from tests.fixtures_kb import quiet_main, cd
from tests.helpers import VaultTestCase, make_vault

HTML1, HTML2, HDR, PNG = b"<p>one</p>", b"<p>two</p>", b"HTTP 200\n", b"\x89PNG fake"


def entry(path, data, **kw):
    return {"path": path, "sha256": sha256_bytes(data), "bytes": len(data), "format": path.rsplit(".", 1)[-1], **kw}


class TestMigrateOriginals(VaultTestCase):
    def old_vault(self, tamper=False):
        o = "originals/page"
        records = {
            "sources/page.md": ({"type": "source", "title": "Page", "kind": "webpage", "tier": "primary",
                                 "url": "https://example.org/page", "originals": [
                                     entry(f"{o}/2026-01-01/index.html", HTML1, captured="2026-01-01T00:00:00Z",
                                           fetched_url="https://example.org/page", http_status=200, via="urllib"),
                                     entry(f"{o}/2026-01-01/headers.txt", HDR, captured="2026-01-01T00:00:00Z", via="urllib"),
                                     entry(f"{o}/2026-01-01-2/index.html", HTML2, captured="2026-01-01T05:00:00Z",
                                           fetched_url="https://example.org/page", http_status=200, via="urllib"),
                                     entry(f"{o}/2026-01-01-2/headers.txt", HDR, captured="2026-01-01T05:00:00Z", via="urllib")],
                                 "accessed": "2026-01-01"}, "\n# Page\n"),
            "sources/tool.md": ({"type": "source", "title": "Tool", "kind": "repo", "tier": "primary",
                                 "url": "https://github.com/example/tool", "originals": [
                                     entry("originals/tool/2026-01-01/commit.txt", b"commit abc\n",
                                           captured="2026-01-01T00:00:00Z", via="git"),
                                     entry("originals/tool/2026-01-01/README.md", b"# Tool\n", via="git")],
                                 "text": f"clone:{self.tmp}/old-clones/example/tool@abc"}, "\n# Tool\n"),
            "sources/paywalled.md": ({"type": "source", "title": "P", "kind": "pdf", "tier": "primary",
                                      "originals": [], "originals_note": "paywalled"}, "\n# P\n"),
        }
        files = {f"{o}/2026-01-01/index.html": HTML1, f"{o}/2026-01-01/headers.txt": HDR,
                 f"{o}/2026-01-01-2/index.html": b"tampered" if tamper else HTML2, f"{o}/2026-01-01-2/headers.txt": HDR,
                 "originals/tool/2026-01-01/commit.txt": b"commit abc\n", "originals/tool/2026-01-01/README.md": b"# Tool\n"}
        (self.tmp / "old-clones/example/tool/.git").mkdir(parents=True)
        return make_vault(self.tmp / "v", records=records, files=files)

    def test_migrate_and_idempotent(self):
        root = self.old_vault()
        cd(self, root)
        rc, out = quiet_main("migrate", "originals", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("would migrate 3 records, 3 capture folders", out)
        self.assertIn("originals:", (root / "sources/page.md").read_text())  # dry run wrote nothing
        self.assertFalse((root / "originals/page/2026-01-01/capture.yaml").exists())

        rc, out = quiet_main("migrate", "originals")
        self.assertEqual(rc, 0, out)
        self.assertIn("mv ", out)  # tells how to move the old clone into the standard root
        page = read_record(root / "sources/page.md").fm
        self.assertNotIn("originals", page)
        self.assertEqual((page["original"], page["sha256"], page["captured"]),
                         ("originals/page/2026-01-01-2/index.html", sha256_bytes(HTML2), "2026-01-01T05:00:00Z"))
        m = yaml.safe_load((root / "originals/page/2026-01-01/capture.yaml").read_text())
        self.assertEqual((m["main"], m["http_status"], m["fetched_url"]), ("index.html", 200, "https://example.org/page"))
        self.assertEqual(sorted(f["name"] for f in m["files"]), ["headers.txt", "index.html"])
        tool = read_record(root / "sources/tool.md").fm
        self.assertEqual(tool["original"], "originals/tool/2026-01-01/commit.txt")
        self.assertEqual(tool["text"], "clone:github.com/example/tool@abc")
        p = read_record(root / "sources/paywalled.md").fm
        self.assertEqual((p.get("original_note"), "originals" in p, "originals_note" in p), ("paywalled", False, False))

        errors = [f for f in Linter(open_vault(start=root), fixity=True).run()
                  if f.severity == "error" and f.rule in ("originals", "manifest", "fixity", "orphan-original")]
        self.assertEqual(errors, [])

        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name != "log.md"}
        rep = migrate_originals(open_vault(start=root))
        self.assertEqual((rep["records"], rep["refused"]), (0, {}))
        self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name != "log.md"})

    def test_refuses_mismatched_folder(self):
        root = self.old_vault(tamper=True)
        rep = migrate_originals(open_vault(start=root))
        self.assertEqual(rep["refused"], {"page": ["originals/page/2026-01-01-2/index.html (sha256 differs)"]})
        self.assertIn("originals:", (root / "sources/page.md").read_text())  # card left as it was
        self.assertFalse((root / "originals/page/2026-01-01-2/capture.yaml").exists())
        self.assertNotIn("originals", read_record(root / "sources/tool.md").fm)  # the others still migrate

    def test_evidence_keeps_captured(self):
        root = make_vault(self.tmp / "v", profile="competition", overrides={"contexts": [{"id": "lab"}]}, records={
            "evidence/lab/shot.md": ({"type": "evidence", "title": "Shot", "kind": "screenshot", "context": "lab",
                                      "captured": "2026-10-24T21:14:05", "captured_from": "filename", "updated": "2026-10-25",
                                      "originals": [entry("evidence/lab/shot.png", PNG, captured="2026-10-24T21:14:05")]},
                                     "\n")}, files={"evidence/lab/shot.png": PNG})
        migrate_originals(open_vault(start=root))
        fm = read_record(root / "evidence/lab/shot.md").fm
        self.assertEqual((fm["original"], fm["sha256"], fm["bytes"], fm["format"], fm["captured"]),
                         ("evidence/lab/shot.png", sha256_bytes(PNG), len(PNG), "png", "2026-10-24T21:14:05"))
        self.assertNotIn("originals", fm)
        self.assertEqual(str(fm["updated"]), "2026-10-25")
        self.assertIn("\nupdated: 2026-10-25\n", (root / "evidence/lab/shot.md").read_text())


class TestMigrateText(VaultTestCase):
    def test_reextract_rewrites_text_keeps_original_and_history(self):
        root = make_vault(self.tmp / "v")
        v = open_vault(start=root)
        p = self.tmp / "plan.txt"
        p.write_text("Plain release plan text for the example project.\n")
        rec = capture.capture_file(v, p)
        tpath = root / "text" / f"{rec.id}.md"
        tpath.write_text(tpath.read_text().replace("Plain release plan", "Garbled"))  # a wrong extraction
        orig = (root / rec.fm["original"]).read_bytes()
        rep = reextract(open_vault(start=root), [rec.id])
        self.assertEqual(rep[0]["status"], "changed")
        self.assertIn("Plain release plan", tpath.read_text())
        self.assertEqual((root / rec.fm["original"]).read_bytes(), orig)
        self.assertEqual(len(list((root / "text" / "history" / rec.id).iterdir())), 1)
        self.assertIn("re-extracted", read_record(rec.path).body)
