import importlib.util
import io
import re
import shutil
import subprocess
import tarfile
import zipfile
from unittest import mock, skipUnless

from kb import extract
from kb.util import KBError, excerpt_in_text
from tests.helpers import VaultTestCase

HAS = {m: importlib.util.find_spec(m) is not None for m in ("trafilatura", "pymupdf", "markitdown", "pptx")}

ARTICLE = ("<html><head><title>Field Notes on Example Widgets</title>"
           "<meta name='author' content='A. Writer'></head><body><nav>Home | About</nav><article>"
           "<h1>Field Notes</h1>" + "<p>Example widgets are made of plain sample material and tested carefully "
           "by the made-up example lab before shipping to customers.</p>" * 6 +
           "<h2>Results</h2><p>The widgets passed the <a href='https://example.org/test'>drop test</a> twice.</p>"
           "</article></body></html>")


def make_pdf(path, pages=("The quick brown fox jumps over the lazy dog on page one.", ""), title="Fox Report"):
    import pymupdf
    doc = pymupdf.open()
    for t in pages:
        p = doc.new_page()
        if t:
            p.insert_text((72, 72), t)
    doc.set_metadata({"title": title, "creationDate": "D:20250301000000"})
    doc.save(path)


class TestExtract(VaultTestCase):
    @skipUnless(HAS["trafilatura"], "trafilatura not installed")
    def test_html_markdown_demoted(self):
        p = self.tmp / "page.html"
        p.write_text(ARTICLE)
        text, meta = extract.extract(p)
        self.assertTrue(meta["extracted_with"].startswith("trafilatura "))
        self.assertTrue(meta["title"].startswith("Field Notes"))
        self.assertFalse(re.search(r"(?m)^#{1,2} ", text), text)
        self.assertIn("### Results", text)
        self.assertIn("[drop test](https://example.org/test)", text)
        self.assertEqual(excerpt_in_text("made of plain sample material and tested carefully", text), "exact")

    @skipUnless(HAS["pymupdf"], "pymupdf not installed")
    def test_pdf_pages_and_ocr_note(self):
        p = self.tmp / "fox.pdf"
        make_pdf(p)
        with mock.patch("kb.ocr.available", return_value=False):
            text, meta = extract.extract(p)
        self.assertIn("[p1]\nThe quick brown fox", text)
        self.assertIn("[p2]", text)
        self.assertEqual(meta["needs_ocr"], [2])
        self.assertEqual(meta["text_quality"], "partial")
        self.assertEqual((meta["title"], meta["issued"]), ("Fox Report", "2025-03-01"))
        with mock.patch("kb.ocr.available", return_value=True), \
                mock.patch("kb.ocr.ocr_image", return_value=("scanned words here", [])):
            text, meta = extract.extract(p)
        self.assertIn("[p2 ocr]\nscanned words here", text)
        self.assertEqual((meta["text_quality"], meta["ocr_pages"]), ("mixed", [2]))

    @skipUnless(HAS["pymupdf"], "pymupdf not installed")
    def test_pdf_image_page_keeps_text_and_adds_ocr(self):
        import pymupdf
        p = self.tmp / "diagram.pdf"
        make_pdf(p, pages=("Plain text page with plenty of words in its text layer.",
                           "Caption text above a big embedded diagram image."))
        doc = pymupdf.open(p)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50))
        pix.clear_with(200)
        doc[1].insert_image(pymupdf.Rect(36, 100, 560, 700), pixmap=pix)
        doc.saveIncr()
        with mock.patch("kb.ocr.available", return_value=True), \
                mock.patch("kb.ocr.ocr_image", return_value=("web01 10.0.0.5", [])) as m:
            text, meta = extract.extract(p)
        self.assertEqual(m.call_count, 1)
        self.assertIn("[p2]\nCaption text", text)
        self.assertIn("[p2 ocr]\nweb01 10.0.0.5", text)
        self.assertNotIn("[p1 ocr]", text)
        self.assertEqual((meta["text_quality"], meta["ocr_pages"]), ("mixed", [2]))
        self.assertEqual(excerpt_in_text("Caption text above a big embedded diagram image. web01 10.0.0.5", text), "exact")

    def test_code_single_block(self):
        p = self.tmp / "tool.py"
        p.write_text("\n".join(f"# line {i}" for i in range(1, 121)))
        text, meta = extract.extract(p)
        lines = text.splitlines()
        self.assertEqual((lines[0], lines[-1]), ("```py", "```"))  # one block; line N = lines[N]
        self.assertEqual(lines[51], "# line 51")
        self.assertEqual(meta["anchors"], "lines")
        self.assertEqual(excerpt_in_text("# line 51", text), "exact")
        outside = re.sub(r"(?s)```.*?```", "", text)
        self.assertNotIn("#", outside)

    def test_markdown_and_text(self):
        p = self.tmp / "notes.md"
        p.write_text("# Top\n\n## Sub\n\nbody\n\n```\n# not a heading\n```\n")
        text, _ = extract.extract(p)
        self.assertIn("### Top\n\n#### Sub", text)
        self.assertIn("# not a heading", text)

    def test_subtitles_dedupe_and_paragraphs(self):
        vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello and welcome\n\n"
               "00:00:03.000 --> 00:00:05.000\nhello and welcome\nto the example talk\n\n"
               "00:00:05.000 --> 00:00:07.000\n<00:00:05.500><c>to the example talk</c>\n\n"
               "00:00:50.000 --> 00:00:52.000\nsecond part starts\n\n"
               "01:00:00.000 --> 01:00:02.000\nan hour in\n")
        p = self.tmp / "talk.en.vtt"
        p.write_text(vtt)
        text, meta = extract.extract(p)
        self.assertEqual(text, "[00:00:01] hello and welcome to the example talk\n\n[00:00:50] second part starts"
                               "\n\n[01:00:00] an hour in\n")
        srt = "1\n00:00:02,000 --> 00:00:04,000\nfirst line\n\n2\n00:00:04,000 --> 00:00:06,000\nsecond line\n"
        (self.tmp / "t.srt").write_text(srt)
        self.assertEqual(extract.extract(self.tmp / "t.srt")[0], "[00:00:02] first line second line\n")

    @skipUnless(HAS["markitdown"] and HAS["pptx"], "markitdown/python-pptx not installed")
    def test_pptx_slides(self):
        from pptx import Presentation
        prs = Presentation()
        for t in ("Alpha", "Beta"):
            s = prs.slides.add_slide(prs.slide_layouts[1])
            s.shapes.title.text = t
            s.placeholders[1].text = f"body {t}"
        p = self.tmp / "deck.pptx"
        prs.save(p)
        text, meta = extract.extract(p)
        self.assertIn("[slide 1]", text)
        self.assertIn("[slide 2]", text)
        self.assertEqual(meta["anchors"], "slides")
        self.assertFalse(re.search(r"(?m)^#{1,2} ", text), text)

    def test_image_uses_ocr(self):
        p = self.tmp / "shot.png"
        p.write_bytes(b"\x89PNG\r\n")
        with mock.patch("kb.ocr.ocr_image", return_value=("OCR TEXT", [])):
            text, meta = extract.extract(p)
        self.assertEqual((text, meta["text_quality"]), ("OCR TEXT\n", "ocr"))

    def test_missing_dependency_hint(self):
        p = self.tmp / "page.html"
        p.write_text("<html></html>")
        with mock.patch("importlib.import_module", side_effect=ImportError):
            with self.assertRaisesRegex(KBError, "pip install trafilatura"):
                extract.extract(p)

    def test_unknown_binary(self):
        p = self.tmp / "blob.bin"
        p.write_bytes(b"\xff\xfe\x00\x81" * 10)
        with self.assertRaises(KBError):
            extract.extract(p)


MEMBERS = (("notes/one.txt", b"first sample\n"), ("two.md", b"second sample\n" * 3))
LISTING = ["notes/one.txt  13  2026-01-02 03:04", "two.md  42  2026-01-02 03:04"]


class TestArchive(VaultTestCase):
    def zip_file(self, name="pack.zip"):
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            for n, data in MEMBERS:
                z.writestr(zipfile.ZipInfo(n, date_time=(2026, 1, 2, 3, 4, 5)), data)
        return p

    def tar_file(self, name="pack.tar.gz", mode="w:gz"):
        p = self.tmp / name
        with tarfile.open(p, mode) as t:
            for n, data in MEMBERS:
                info = tarfile.TarInfo(n)
                info.size, info.mtime = len(data), 1767312000  # 2026-01-02 00:00 UTC
                t.addfile(info, io.BytesIO(data))
        return p

    def test_zip_and_tar_gz_list_members(self):
        text, meta = extract.extract(self.zip_file())
        self.assertEqual(text.splitlines(), LISTING)
        self.assertEqual((meta["extracted_with"], meta["text_quality"]), ("kb archive", "manual"))
        text, _ = extract.extract(self.tar_file())
        self.assertEqual(text.splitlines(), ["notes/one.txt  13  2026-01-02 00:00", "two.md  42  2026-01-02 00:00"])
        self.assertEqual(extract.detect(self.zip_file()), "archive")

    def test_tgz_and_plain_tar_are_archives(self):
        self.assertEqual(extract.detect(self.tar_file("pack.tgz")), "archive")
        self.assertEqual(extract.detect(self.tar_file("pack.tar", "w")), "archive")

    @skipUnless(shutil.which("zstd"), "zstd CLI not installed")
    def test_tar_zst_lists_members_through_zstd(self):
        tar = self.tar_file("pack.tar", "w")
        p = self.tmp / "pack.tar.zst"
        p.write_bytes(subprocess.run(["zstd", "-q", "-c", str(tar)], capture_output=True, check=True).stdout)
        text, meta = extract.extract(p)
        self.assertEqual([l.split("  ")[:2] for l in text.splitlines()], [["notes/one.txt", "13"], ["two.md", "42"]])
        self.assertEqual(meta["text_quality"], "manual")

    @skipUnless(shutil.which("zstd"), "zstd CLI not installed")
    def test_zst_that_is_not_a_tar_has_no_listing(self):
        p = self.tmp / "notes.zst"
        p.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=b"just text\n" * 50, capture_output=True,
                                     check=True).stdout)
        text, meta = extract.extract(p)
        self.assertEqual(text.strip(), "")
        self.assertIn("not a tar", meta["note"])

    def test_zst_without_zstd_has_note_and_no_listing(self):
        p = self.tmp / "pack.tar.zst"
        p.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 8)
        with mock.patch("kb.extract.shutil.which", return_value=None):
            text, meta = extract.extract(p)
        self.assertEqual(text.strip(), "")
        self.assertIn("zstd CLI is not installed", meta["note"])
        self.assertEqual(meta["text_quality"], "none")

    def test_corrupt_zip_says_what_failed(self):
        p = self.tmp / "bad.zip"
        p.write_bytes(b"PK\x03\x04 not really a zip")
        with self.assertRaisesRegex(KBError, "could not list bad.zip"):
            extract.extract(p)
