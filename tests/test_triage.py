import io
import json
import sys
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from kb import triage
from kb.records import read_record
from kb.util import KBError, sha256_bytes
from kb.vault import open_vault
from tests.helpers import VaultTestCase, make_vault

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

OCR_TEXT = "root@attacker:~# crackmapexec smb 192.0.2.10\npassword=Example123x\nHost web-01 pwned"
OCR_WORDS = [{"text": w, "left": i * 10, "top": 0, "width": 9, "height": 9, "conf": 90.0}
             for i, w in enumerate(OCR_TEXT.split())]


def png_bytes(color="red", exif_date=None) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (8, 8), color)
    if exif_date:
        ex = Image.Exif()
        ex[306] = exif_date
        im.save(buf, "PNG", exif=ex)
    else:
        im.save(buf, "PNG")
    return buf.getvalue()


def zip_bytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def fake_capture(calls):
    mod = types.ModuleType("kb.capture")

    def capture_file(vault, path, id=None, type="source", kind=None, obtained_from=None):
        calls.append(("file", Path(path).name, obtained_from))
        return types.SimpleNamespace(id=id or "captured-file")

    def capture_url(vault, url, id=None, type="source", kind=None, obtained_from=None):
        calls.append(("url", url, obtained_from))
        return types.SimpleNamespace(id="src-" + url.rsplit("/", 1)[-1])

    mod.capture_file, mod.capture_url = capture_file, capture_url
    return mod


class TestHelpers(VaultTestCase):
    def test_detect_kind(self):
        cases = {"a.pdf": b"%PDF-1.7\n", "a.pcap": b"\xd4\xc3\xb2\xa1" + b"\0" * 20,
                 "n.xml": b'<?xml version="1.0"?><nmaprun scanner="nmap">', "w.md": b"# Write-up\n\nWe did a thing.\n",
                 "u.txt": b"https://example.com/a\n- https://example.org/b\n", "x.log": b"line one\n",
                 "d.json": b'{"a": 1}', "z.zip": zip_bytes({"a.txt": "x"}), "b.docx": zip_bytes({"word/x": "y"}),
                 "bin.dat": b"\x00\x01\x02"}
        want = {"a.pdf": "pdf", "a.pcap": "pcap", "n.xml": "nmap", "w.md": "markdown", "u.txt": "urllist",
                "x.log": "log", "d.json": "json", "z.zip": "archive", "b.docx": "office", "bin.dat": "binary"}
        for name, data in cases.items():
            (self.tmp / name).write_bytes(data)
            self.assertEqual(triage.detect_kind(self.tmp / name), want[name], name)

    def test_unpack_guards(self):
        evil = self.tmp / "evil.zip"
        evil.write_bytes(zip_bytes({"ok.txt": "fine", "../../escape.txt": "nope"}))
        with self.assertRaises(KBError):
            triage.unpack(evil, self.tmp / "out", 1 << 20)
        self.assertFalse((self.tmp / "escape.txt").exists())
        bomb = self.tmp / "bomb.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("zeros.bin", b"\0" * 200_000)
        bomb.write_bytes(buf.getvalue())
        with self.assertRaisesRegex(KBError, "more than"):
            triage.unpack(bomb, self.tmp / "out2", 100_000)
        ok = self.tmp / "ok.zip"
        ok.write_bytes(zip_bytes({"img/57.png": "x", "notes.md": "y"}))
        self.assertEqual(sorted(n for n, _ in triage.unpack(ok, self.tmp / "out3", 1 << 20)),
                         ["img/57.png", "notes.md"])

    @unittest.skipUnless(Image, "Pillow not installed")
    def test_captured_time(self):
        p = self.tmp / "shot.png"
        p.write_bytes(png_bytes(exif_date="2026:10:24 21:14:05"))
        self.assertEqual(triage.captured_time(p, p.name, True), ("2026-10-24T21:14:05", "exif"))
        q = self.tmp / "Screenshot 2026-10-24 at 21.15.06.png"
        q.write_bytes(png_bytes())
        self.assertEqual(triage.captured_time(q, q.name, True), ("2026-10-24T21:15:06", "filename"))
        self.assertEqual(triage.captured_time(self.tmp / "shot.png", "plain.png", False)[1], "mtime")


@unittest.skipUnless(Image, "Pillow not installed")
class TestTriageFlow(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.shot = png_bytes("red")
        stored = png_bytes("blue")
        from kb.util import sha256_bytes
        self.root = make_vault(
            self.tmp / "v", profile="competition", overrides={"contexts": [{"id": "ctx1"}]},
            records={
                "entities/host/web-01.md": ({"type": "entity", "title": "web-01", "kind": "host",
                                             "handles": ["ip:192.0.2.10", "hostname:web-01"]}, "\n"),
                "evidence/ctx1/old-shot.md": ({"type": "evidence", "title": "Old", "kind": "screenshot",
                                               "context": "ctx1", "original": "evidence/ctx1/old-shot.png",
                                               "sha256": sha256_bytes(stored)}, "\n"),
            },
            files={
                "evidence/ctx1/old-shot.png": stored,
                "inbox/alice/Screenshot 2026-10-24 at 21.14.05.png": self.shot,
                "inbox/alice/copy.png": self.shot,                       # duplicate within the batch
                "inbox/alice/again.png": stored,                         # duplicate of a stored original
                "inbox/alice/links.txt": "https://example.com/one\nhttps://example.org/two\n",
                "inbox/alice/writeup.md": "# Our write-up\n\nWe scanned web-01.\n",
                "inbox/bob/paper.pdf": b"%PDF-1.4\nfake\n",
                "inbox/bob/pack.zip": zip_bytes({"img/57.png": png_bytes("green"), "notes/what.bin": b"\x00\x01"}),
                "inbox/discover-2026-10-27.md": "# queue\n",
            })
        self.v = open_vault(start=self.root)

    def _scan(self):
        with mock.patch.object(triage.ocr, "available", return_value=True), \
                mock.patch.object(triage.ocr, "ocr_image", return_value=(OCR_TEXT, OCR_WORDS)):
            r = triage.scan(self.v)
        props = [json.loads(l) for l in (self.root / r["proposals"]).read_text().splitlines()]
        return r, {p["obtained_from"]: p for p in props}

    def _save(self, run, props):
        path = self.root / ".kb" / "triage" / run / "proposals.jsonl"
        path.write_text("".join(json.dumps(p) + "\n" for p in props.values()))

    def test_scan_proposals(self):
        r, props = self._scan()
        self.assertNotIn("inbox/discover-2026-10-27.md", props)
        shot = props["inbox/alice/Screenshot 2026-10-24 at 21.14.05.png"]
        self.assertEqual(list(shot), list(triage.FIELDS))
        self.assertEqual((shot["route"], shot["type"], shot["id"], shot["context"]),
                         ("evidence", "evidence", "ctx1-alice-0001", "ctx1"))
        self.assertEqual((shot["captured"], shot["captured_from"]), ("2026-10-24T21:14:05", "filename"))
        self.assertIn("[[web-01]]", shot["suggest_links"])
        self.assertEqual(shot["sensitive"], ["secrets"])
        self.assertIn("crackmapexec", shot["ocr_excerpt"])
        self.assertEqual(props["inbox/alice/copy.png"]["route"], "duplicate")
        self.assertEqual(props["inbox/alice/again.png"]["reason"], "duplicate-of old-shot")
        self.assertEqual(props["inbox/alice/links.txt"]["route"], "capture")
        self.assertEqual(props["inbox/alice/links.txt"]["links"], ["https://example.com/one", "https://example.org/two"])
        self.assertEqual(props["inbox/alice/writeup.md"]["route"], "note")
        self.assertEqual(props["inbox/bob/paper.pdf"]["route"], "source")
        member = props["inbox/bob/pack.zip:img/57.png"]
        self.assertEqual((member["route"], member["id"], member["contributor"]), ("evidence", "ctx1-bob-0001", "bob"))
        self.assertTrue(member["file"].startswith(".kb/triage/"))
        self.assertEqual(props["inbox/bob/pack.zip:notes/what.bin"]["route"], "hold")
        self.assertEqual(r["secrets"][0]["kinds"], ["assignment"])
        hits = json.loads(next((self.root / ".kb/triage" / r["run"]).glob("*.hits.json")).read_text())
        self.assertNotIn("Example123x", json.dumps(hits))  # never store the secret itself
        self.assertTrue(hits["hits"][0]["boxes"])
        self.assertIn(f"kb triage apply {r['run']}", "\n".join(triage._scan_text(r)))

    def test_apply(self):
        r, props = self._scan()
        run = r["run"]
        with self.assertRaisesRegex(KBError, "title and a description"):
            triage.apply(self.v, run)
        shot = props["inbox/alice/Screenshot 2026-10-24 at 21.14.05.png"]
        shot.update(title="SMB scan of web-01", description="Terminal running crackmapexec against 192.0.2.10.",
                    phase="recon", links=["[[web-01]]"], supports=["finding-x"])
        props["inbox/bob/pack.zip:img/57.png"].update(title="Green", description="A green square.")
        props["inbox/bob/pack.zip:notes/what.bin"]["question"] = "what is this binary?"
        self._save(run, props)
        calls = []
        with mock.patch.dict(sys.modules, {"kb.capture": fake_capture(calls)}):
            res = triage.apply(self.v, run)
        ev = self.root / "evidence/ctx1"
        self.assertEqual((ev / "ctx1-alice-0001.png").read_bytes(), self.shot)
        rec = read_record(ev / "ctx1-alice-0001.md")
        fm = rec.fm
        self.assertEqual((fm["type"], fm["kind"], fm["file"], fm["contributor"], fm["phase"]),
                         ("evidence", "screenshot", "[[ctx1-alice-0001.png]]", ["alice"], "recon"))
        self.assertEqual(fm["hosts"], ["[[web-01]]"])
        self.assertEqual(fm["supports"], ["[[finding-x]]"])
        self.assertEqual((fm["original"], fm["sha256"], fm["format"]),
                         ("evidence/ctx1/ctx1-alice-0001.png", sha256_bytes(self.shot), "png"))
        self.assertEqual(fm["bytes"], len(self.shot))
        self.assertNotIn("originals", fm)
        self.assertEqual((fm["captured"], fm["captured_from"]), ("2026-10-24T21:14:05", "filename"))  # no tz known: no Z
        self.assertIn("\nupdated: 20", (ev / "ctx1-alice-0001.md").read_text())  # a YAML date, not a quoted string
        self.assertEqual(fm["sensitive"], ["secrets"])
        self.assertIn("![[ctx1-alice-0001.png]]", rec.body)
        self.assertIn("> [!quote]- OCR\n> root@attacker", rec.body)
        self.assertIn("## Contributor notes", rec.body)
        note = read_record(self.root / "notes/writeup.md")
        self.assertEqual((note.fm["type"], note.fm["title"], note.fm["contributor"]), ("note", "Our write-up", ["alice"]))
        self.assertIn("We scanned web-01.", note.body)
        self.assertIn(("file", "paper.pdf", "inbox/bob/paper.pdf"), calls)
        self.assertIn(("url", "https://example.org/two", "inbox/alice/links.txt"), calls)
        # inbox: only the held archive (one member is held) and the queue file are left
        left = sorted(p.relative_to(self.root / "inbox").as_posix() for p in (self.root / "inbox").rglob("*")
                      if p.is_file())
        self.assertEqual(left, ["bob/pack.zip", "discover-2026-10-27.md"])
        rows = [l.split("\t") for l in (self.root / res["manifest"]).read_text().splitlines()[1:]]
        disp = {r[0]: (r[2], r[3]) for r in rows}
        self.assertEqual(disp["inbox/alice/again.png"], ("duplicate-of", "old-shot"))
        self.assertEqual(disp["inbox/alice/links.txt"], ("routed", "src-one src-two"))
        self.assertEqual(disp["inbox/bob/pack.zip"][0], "extracted-from-archive")
        self.assertEqual(disp["inbox/bob/pack.zip:notes/what.bin"], ("held", "what is this binary?"))
        self.assertTrue(all(r[2] for r in rows))
        self.assertIn(f"triage | {run}", (self.root / "log.md").read_text())
        # resumable and idempotent: a rerun changes nothing and calls nothing
        calls.clear()
        with mock.patch.dict(sys.modules, {"kb.capture": fake_capture(calls)}):
            res2 = triage.apply(open_vault(start=self.root), run)
        self.assertEqual(calls, [])
        self.assertEqual(res2["counts"], res["counts"])
        st = triage.status(open_vault(start=self.root))
        self.assertEqual(st["pending_runs"], [])
        self.assertEqual(st["inbox"], {"bob": 1})
        self.assertEqual(st["held"][0]["question"], "what is this binary?")

    def test_capture_unavailable_holds(self):
        r, props = self._scan()
        keep = {k: p for k, p in props.items() if p["route"] in ("source", "capture")}
        self._save(r["run"], keep)
        with mock.patch.dict(sys.modules, {"kb.capture": None}):
            res = triage.apply(self.v, r["run"])
        self.assertEqual({h["question"] for h in res["held"]}, {"kb capture not available"})
        self.assertTrue((self.root / "inbox/bob/paper.pdf").exists())
        self.assertEqual(triage.status(open_vault(start=self.root))["pending_runs"], [])

    def test_status_pending_and_cli(self):
        from kb.cli import main
        r, _ = self._scan()
        self.assertEqual(triage.status(self.v)["pending_runs"], [r["run"]])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, mock.patch("os.getcwd", return_value=str(self.root)):
            self.assertEqual(main(["triage", "status", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["inbox"]["alice"], 5)
        with self.assertRaisesRegex(KBError, "no triage run"):
            triage.apply(self.v, "20990101-000000")


if __name__ == "__main__":
    unittest.main()
