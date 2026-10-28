"""Text extraction with anchors: extract(path, kind=None) -> (text, meta).

meta always has extracted_with, text_quality (good|mixed|ocr|partial|none|manual) and anchors; it may add
hints from the tool (title, issued, author) that the agent should confirm, and ocr_pages/needs_ocr.
"""

import importlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

from kb import ocr
from kb.util import KBError

EXT = {
    "html": {".html", ".htm", ".xhtml"},
    "pdf": {".pdf"},
    "office": {".pptx", ".docx", ".xlsx", ".xls"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"},
    "subtitles": {".vtt", ".srt"},
    "code": {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
             ".java", ".kt", ".swift", ".cs", ".rb", ".php", ".pl", ".lua", ".sh", ".bash", ".zsh", ".ps1",
             ".sql", ".yaml", ".yml", ".toml", ".json", ".xml", ".ini", ".cfg", ".conf", ".tf", ".nix"},
    "text": {".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv"},
    "archive": {".zip", ".tar", ".tar.gz", ".tgz", ".tar.zst", ".zst"},
}
KINDS = (*EXT, "chat")  # chat: a DiscordChatExporter JSON export, picked only by --kind chat
# Source kinds (profile `kind` values) that imply an extractor regardless of extension.
ALIASES = {"webpage": "html", "blog-post": "html", "slides": "office", "office-doc": "office",
           "spreadsheet": "office", "video": "subtitles", "podcast": "subtitles"}
INSTALL = {"trafilatura": "pip install trafilatura", "pymupdf": "pip install pymupdf",
           "markitdown": "pip install 'markitdown[pptx,docx,xlsx]'"}
PARA_SECONDS = 45
IMAGE_SHARE = 0.3  # PDF pages with images over this share of the page are OCRed too


def detect(path, kind=None) -> str:
    kind = ALIASES.get(kind, kind)
    if kind in KINDS:
        return kind
    name = Path(path).name.lower()  # endswith, not suffix: ".tar.gz" has two suffixes
    for k, exts in EXT.items():
        if any(name.endswith(e) for e in exts):
            return k
    head = Path(path).read_bytes()[:2048]
    if head.startswith(b"%PDF"):
        return "pdf"
    if re.search(rb"(?i)<!doctype html|<html", head):
        return "html"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        raise KBError(f"don't know how to extract text from {Path(path).name}; pass --kind "
                      f"({', '.join(KINDS)}) or capture it with originals only") from None


def extract(path, kind=None) -> tuple[str, dict]:
    path = Path(path)
    k = detect(path, kind)
    text, meta = globals()[f"_{k}"](path)
    meta.setdefault("text_quality", "good" if text.strip() else "none")
    return text.rstrip() + "\n", meta


def _need(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        raise KBError(f"{module} is not installed; install it: {INSTALL[module]}") from None


def _version(dist: str) -> str:
    try:
        return f"{dist} {importlib.metadata.version(dist)}"
    except importlib.metadata.PackageNotFoundError:
        return dist


_HEAD = re.compile(r"^(#{1,6})(\s+\S.*)$")


def demote_headings(md: str, top: int = 3) -> str:
    """Shift Markdown headings so the highest becomes `top` (### by default), keeping their nesting.
    Skips fenced code blocks."""
    lines, fence = md.splitlines(), False
    levels = []
    for line in lines:
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and (m := _HEAD.match(line)):
            levels.append(len(m.group(1)))
    if not levels or min(levels) >= top:
        return md
    shift, out, fence = top - min(levels), [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and (m := _HEAD.match(line)):
            line = "#" * min(6, len(m.group(1)) + shift) + m.group(2)
        out.append(line)
    return "\n".join(out)


# --- extractors --------------------------------------------------------------------

def _html(path):
    traf = _need("trafilatura")
    raw = path.read_bytes()
    md = traf.extract(raw, output_format="markdown", include_links=True, include_tables=True,
                      include_comments=False, favor_recall=True) or ""
    meta = {"extracted_with": f"trafilatura {traf.__version__}", "anchors": "headings"}
    try:
        info = traf.extract_metadata(raw)
    except Exception:  # metadata is a hint; never fail the capture over it
        info = None
    if info is not None:
        if info.title:
            meta["title"] = info.title
        if info.date:
            meta["issued"] = info.date
        if info.author:
            meta["author"] = [a.strip() for a in re.split(r";|\s+and\s+", info.author) if a.strip()]
    if len(md) < 500:
        meta["text_quality"] = "partial" if md.strip() else "none"
        meta["note"] = "little text extracted; the page may need JavaScript (single-file) or be a shell"
    return demote_headings(md), meta


def _image_share(pymupdf, page) -> float:
    """Share of the page area covered by embedded images (overlaps counted twice; capped at 1)."""
    area = sum(abs(pymupdf.Rect(i["bbox"]) & page.rect) for i in page.get_image_info())
    return min(1.0, area / abs(page.rect))


def _pdf(path):
    pymupdf = _need("pymupdf")
    doc = pymupdf.open(path)
    parts, low, ocr_done = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for i, page in enumerate(doc, 1):
            t = page.get_text().strip()
            sparse = len(t) < 20
            if sparse:
                low.append(i)
            if (sparse or _image_share(pymupdf, page) >= IMAGE_SHARE) and ocr.available():
                png = Path(td) / f"p{i}.png"
                page.get_pixmap(dpi=250).save(png)
                o = ocr.ocr_image(png)[0].strip()
                ocr_done.append(i)
                if o:
                    parts.append((f"[p{i}]\n{t}\n\n" if t else "") + f"[p{i} ocr]\n{o}\n")
                    continue
            parts.append(f"[p{i}]\n{t}\n")
    meta = {"extracted_with": f"pymupdf {pymupdf.__version__}" + (" + tesseract" if ocr_done else ""),
            "anchors": "pages", "pages": len(parts)}
    if low:
        meta["needs_ocr"] = low
    if low or ocr_done:
        meta["ocr_pages"] = ocr_done
        meta["text_quality"] = ("ocr" if len(ocr_done) == len(parts) else "mixed") if ocr_done else "partial"
    info = doc.metadata or {}
    if info.get("title", "").strip():
        meta["title"] = info["title"].strip()
    if info.get("author", "").strip():
        meta["author"] = [info["author"].strip()]
    if m := re.match(r"D:(\d{4})(\d{2})(\d{2})", info.get("creationDate") or ""):
        meta["issued"] = "-".join(m.groups())
    return "\n".join(parts), meta


_SLIDE = re.compile(r"(?m)^<!-- Slide number: (\d+) -->$")


def _office(path):
    mid = _need("markitdown")
    try:
        res = mid.MarkItDown().convert(str(path))
    except Exception as e:
        raise KBError(f"markitdown could not convert {path.name}: {e}; for xlsx/docx/pptx install "
                      f"{INSTALL['markitdown']}") from None
    md = res.text_content or ""
    anchors = "none"
    if _SLIDE.search(md):
        md, anchors = _SLIDE.sub(r"[slide \1]", md), "slides"
    elif path.suffix.lower() in (".xlsx", ".xls"):
        md, anchors = re.sub(r"(?m)^## (.+)$", r"### sheet: \1", md), "sheets"
    meta = {"extracted_with": _version("markitdown"), "anchors": anchors}
    if getattr(res, "title", None):
        meta["title"] = res.title
    return demote_headings(md), meta


def _image(path):
    text, _words = ocr.ocr_image(path)
    return text, {"extracted_with": "tesseract", "anchors": "none", "text_quality": "ocr" if text.strip() else "none"}


def _read_text(path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _text(path):
    t = _read_text(path)
    if path.suffix.lower() in (".md", ".markdown"):
        t = demote_headings(t)
    return t, {"extracted_with": "kb", "anchors": "none"}


def _code(path):
    """One fenced block for the whole file: line N inside the fence is line N of the original
    (pretty-printed for JSON), which is what `Location: L40-52` refers to."""
    raw = _read_text(path)
    lang = path.suffix.lower().lstrip(".")
    if lang == "json" or raw.lstrip()[:1] in ("{", "["):
        try:  # pretty-print so JSON (API captures) can be quoted line by line
            raw = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
            lang = "json"
        except ValueError:
            pass
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`{3,}", raw)), default=0) + 1)
    return f"{fence}{lang}\n{raw.rstrip()}\n{fence}\n", {"extracted_with": "kb", "anchors": "lines"}


def _archive(path):
    """Member listing, one `name  size  date` line per member; the archive itself is the original.
    Nothing is written to disk, so member names cannot escape anywhere."""
    name = path.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                rows = [(i.filename, i.file_size, "%04d-%02d-%02d %02d:%02d" % i.date_time[:5]) for i in z.infolist()]
        elif name.endswith(".zst"):
            rows, why = _zst_rows(path)
            if rows is None:
                return "", {"extracted_with": "kb archive", "anchors": "none", "text_quality": "none", "note": why}
        else:
            with tarfile.open(path, "r:*") as t:
                rows = _tar_rows(t)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise KBError(f"could not list {path.name}: {e}") from None
    text = "\n".join(f"{n}  {s}  {d}" for n, s, d in rows)
    return text, {"extracted_with": "kb archive", "anchors": "none", "text_quality": "manual"}


def _tar_rows(t):
    return [(m.name, m.size, time.strftime("%Y-%m-%d %H:%M", time.gmtime(m.mtime))) for m in t]


def _zst_rows(path):
    """(rows, None) for a .tar.zst read through the zstd CLI (the stdlib has no zstd), or (None, why)
    when there is no listing: zstd is missing or the payload is not a tar."""
    if not shutil.which("zstd"):
        return None, "no member listing: the zstd CLI is not installed (apt install zstd)"
    proc = subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as t:
            rows = _tar_rows(t)
    except tarfile.TarError:
        rows = None
    _, err = proc.communicate()  # reads the rest of the stream, so zstd can exit
    if proc.returncode:
        raise KBError(f"zstd could not decompress {path.name}: {err.decode(errors='replace').strip()}")
    return (rows, None) if rows is not None else (None, "no member listing: not a tar archive")


_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->")


def parse_subtitles(raw: str) -> list[tuple[float, str]]:
    """(start_seconds, line) for each caption line, with rolling-caption repeats removed."""
    out, last = [], None
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None or not (m := _TS.search(lines[idx])):
            continue
        start = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        for line in lines[idx + 1:]:
            line = re.sub(r"<[^>]+>", "", line).replace("&nbsp;", " ").strip()
            if line and line != last:
                out.append((start, line))
                last = line
    return out


def _chat_export(path):
    """The parsed DiscordChatExporter JSON export, or None when the file isn't one."""
    try:
        d = json.loads(_read_text(path))
    except ValueError:
        return None
    return d if isinstance(d, dict) and {"guild", "channel", "messages"} <= d.keys() else None


def chat_attachments(path) -> list[str]:
    """Paths (relative to the export) of every message attachment in a chat export; avatars, emoji and
    embed thumbnails are not attachments."""
    d = _chat_export(path) or {"messages": []}
    return list(dict.fromkeys(a["url"] for m in d["messages"] for a in m.get("attachments") or []))


def _chat(path):
    """Transcript of a DiscordChatExporter JSON export, one block per message with its link; each attachment
    (copied next to the export by capture) is inlined: OCR for images, text for text files, a listing for
    archives. Excerpts quote these lines, so one attachment is citable as one excerpt."""
    d = _chat_export(path)
    if d is None:
        return _text(path)
    g, c = d["guild"], d["channel"]
    out = [f"Discord: {g.get('name')} / {c.get('category')} / {c.get('name')} ({len(d['messages'])} messages)\n"
           f"Message links: https://discord.com/channels/{g['id']}/{c['id']}/<msg id>"]
    ocred = False
    for m in d["messages"]:
        a = m.get("author") or {}
        who = f"{a['nickname']} ({a['name']})" if a.get("nickname") and a["nickname"] != a.get("name") else a.get("name")
        ref = (m.get("reference") or {}).get("messageId")
        out.append(f"[msg {m['id']} {m['timestamp'][:16].replace('T', ' ')} UTC] {who}"
                   + (f" (reply to msg {ref})" if ref else ""))
        if (m.get("content") or "").strip():
            out.append(m["content"].strip())
        out += [f"[embed] {e.get('title') or ''} {e.get('url') or ''}".rstrip() for e in m.get("embeds") or []
                if e.get("url") and e["url"] not in (m.get("content") or "")]
        for att in m.get("attachments") or []:
            f = path.parent / Path(att["url"]).name
            out.append(f"[attachment {f.name}: {att.get('fileName')}]")
            if not f.is_file():
                out.append("(file missing from the export)")
                continue
            try:
                k = detect(f)
            except KBError:  # binary without a known extension: kept as an original, not extracted
                continue
            if k == "image" and ocr.available():
                text, _ = ocr.ocr_image(f)
                ocred = True
                out.append(text.strip() or "(no text found by OCR)")
            elif k in ("text", "code"):
                out.append(_read_text(f).strip())
            elif k == "archive":
                out.append(_archive(f)[0])
    return "\n\n".join(out), {"extracted_with": "kb chat (DiscordChatExporter json)", "anchors": "messages",
                               "text_quality": "mixed" if ocred else "good"}


def _subtitles(path):
    paras, cur, cur_start = [], [], None
    for start, line in parse_subtitles(_read_text(path)):
        if cur_start is None or start - cur_start >= PARA_SECONDS:
            if cur:
                paras.append((cur_start, cur))
            cur, cur_start = [], start
        cur.append(line)
    if cur:
        paras.append((cur_start, cur))
    text = "\n\n".join(f"[{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}] " + " ".join(ls) for s, ls in paras)
    return text, {"extracted_with": "kb subtitles", "anchors": "timestamps"}
