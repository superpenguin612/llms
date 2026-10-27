"""OCR via the tesseract CLI: text plus word boxes (for later redaction by domain skills)."""

import csv
import io
import shutil
import subprocess

from kb.util import KBError


def available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(path, lang: str = "eng", timeout: int = 120) -> tuple[str, list[dict]]:
    """Return (text, words). Each word: {text, left, top, width, height, conf}."""
    if not available():
        raise KBError("tesseract not found; install it (apt install tesseract-ocr) to OCR images")
    try:
        out = subprocess.run(["tesseract", str(path), "stdout", "-l", lang, "tsv"],
                             capture_output=True, text=True, timeout=timeout, check=True).stdout
    except subprocess.CalledProcessError as e:
        raise KBError(f"tesseract failed on {path}: {e.stderr.strip().splitlines()[-1] if e.stderr else e}") from None
    except subprocess.TimeoutExpired:
        raise KBError(f"tesseract timed out after {timeout}s on {path}") from None
    words, lines, cur = [], [], None
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        txt = (row.get("text") or "").strip()
        if not txt:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1.0
        words.append({"text": txt, "left": int(row["left"]), "top": int(row["top"]),
                      "width": int(row["width"]), "height": int(row["height"]), "conf": conf})
        key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
        if key != cur:
            lines.append([])
            cur = key
        lines[-1].append(txt)
    return "\n".join(" ".join(l) for l in lines), words
