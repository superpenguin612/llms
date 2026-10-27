"""Small shared helpers: hashing, time, text normalization, excerpt matching, cadences, output."""

import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path


class KBError(Exception):
    """A user-facing error. The message should say how to fix the problem."""


# --- hashing and files -------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path, data) -> None:
    """Write text or bytes to path via a temp file + rename, creating parents."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- time --------------------------------------------------------------------

def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return now_utc().strftime("%Y-%m-%d")


def parse_time(value):
    """Parse an ISO date or datetime (str/date/datetime) into an aware UTC datetime, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m", "%Y"):
        try:
            t = dt.datetime.fromisoformat(s) if fmt is None else dt.datetime.strptime(s, fmt)
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


_CADENCE_WORDS = {"hourly": "1h", "daily": "1d", "weekly": "7d", "monthly": "30d", "quarterly": "91d"}


def parse_cadence(value):
    """'1h' / '12h' / '1d' / '2w' / 'weekly' / 'monthly' -> timedelta; 'never' -> None;
    'before-event' -> the string 'before-event'."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("never", "none", ""):
        return None
    if s == "before-event":
        return "before-event"
    s = _CADENCE_WORDS.get(s, s)
    m = re.fullmatch(r"(\d+)\s*([mhdw])", s)
    if not m:
        raise KBError(f"bad cadence {value!r}: use e.g. 1h, 12h, 1d, 2w, weekly, monthly, never, before-event")
    n, unit = int(m.group(1)), m.group(2)
    return {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n),
            "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n)}[unit]


def is_due(last_checked, cadence, now=None, event_dates=(), lead=dt.timedelta(days=3)) -> bool:
    """True when a record with this cadence should be checked again.
    before-event: due if an event starts within `lead` and we haven't checked since lead began."""
    now = now or now_utc()
    cad = parse_cadence(cadence)
    last = parse_time(last_checked)
    if cad is None:
        return False
    if cad == "before-event":
        for d in event_dates:
            start = parse_time(d)
            if start and start - lead <= now <= start + dt.timedelta(days=1):
                return last is None or last < start - lead
        return False
    return last is None or now - last >= cad


# --- text normalization and excerpt matching -----------------------------------

_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "…": "...",
})
_ANCHOR = re.compile(r"\[(?:p\d+(?: ocr)?|slide \d+|\d{1,2}:\d{2}(?::\d{2})?|L\d+)\]", re.I)
_MD = re.compile(r"(\*\*|__|\*|`+|~~)")
_QUOTE_MARK = re.compile(r"(?m)^\s*>\s?")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‍⁠﻿]")
_GAP = re.compile(r"\[\s*(?:…|\.\.\.)\s*\]")
_MDLINK = re.compile(r"!?\[([^\[\]]*)\]\([^()\s]*(?:\([^()\s]*\)[^()\s]*)*(?:\s+\"[^\"]*\")?\)")


def normalize(text: str) -> str:
    """Normalize for verbatim comparison (spec §2.6)."""
    t = unicodedata.normalize("NFKC", text or "")
    t = html.unescape(t).translate(_FOLD)
    t = _CTRL.sub("", t)
    t = _QUOTE_MARK.sub("", t)
    t = _MDLINK.sub(r"\1", t)  # [text](url) and ![alt](src) -> text; extractors keep links
    t = _ANCHOR.sub(" ", t)
    t = _MD.sub("", t)
    t = re.sub(r"(?<=\S)([-\\/])[ \t]*\n\s*(?=\S)", r"\1", t)  # rejoin "AES-\nCFB8", "Policy\\\nSecrets" wraps
    return re.sub(r"\s+", " ", t).strip().casefold()


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text)


def excerpt_in_text(excerpt: str, text: str, normalized_text: str | None = None) -> str:
    """'exact' if every [...]-separated segment of the excerpt appears in order in the text after
    normalization, 'loose' if it only matches after reducing to alphanumerics, else 'missing'."""
    nt = normalized_text if normalized_text is not None else normalize(text)
    segments = [normalize(s) for s in _GAP.split(excerpt or "")]
    segments = [s for s in segments if s]
    if not segments:
        return "missing"

    def in_order(hay, needles):
        pos = 0
        for n in needles:
            i = hay.find(n, pos)
            if i < 0:
                return False
            pos = i + len(n)
        return True

    if in_order(nt, segments):
        return "exact"
    if in_order(_alnum(nt), [_alnum(s) for s in segments if _alnum(s)]):
        return "loose"
    return "missing"


def slugify(text: str, max_len: int = 60) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t[:max_len].rstrip("-") or "untitled"


# --- vault log and output --------------------------------------------------------

def append_log(root, op: str, ref: str, detail: str = "") -> None:
    """Append `## [YYYY-MM-DD] op | ref | detail` to <root>/log.md."""
    path = Path(root) / "log.md"
    line = f"## [{today()}] {op} | {ref}" + (f" | {detail}" if detail else "") + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def emit(args, data, text=None) -> None:
    """Print `data` as JSON when --json was given, otherwise `text` (str, list of lines, or callable)."""
    if getattr(args, "json", False):
        json.dump(data, sys.stdout, indent=2, default=str, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    if callable(text):
        text = text(data)
    if text is None:
        text = data if isinstance(data, str) else json.dumps(data, indent=2, default=str, ensure_ascii=False)
    if isinstance(text, (list, tuple)):
        text = "\n".join(text)
    if text:
        print(text)
