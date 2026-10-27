"""kb triage: scan the inbox into reviewable proposals, then apply the reviewed proposals."""

import datetime as dt
import importlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from kb import capture, ocr, secrets
from kb.profile import check_value
from kb.records import dump_front_matter, split_front_matter, write_record
from kb.util import KBError, append_log, atomic_write, emit, iso, now_utc, sha256_file, slugify, today
from kb.vault import open_from_args

ROUTES = ("evidence", "source", "capture", "note", "entity-update", "duplicate", "exclude", "hold")
FIELDS = ("file", "obtained_from", "sha256", "contributor", "route", "type", "id", "context", "captured",
          "captured_from", "ocr_excerpt", "suggest_links", "sensitive", "title", "description", "phase",
          "links", "supports", "reason", "question", "set")
NEXT_STEP = ("review proposals.jsonl: view each image, fill title/description/phase/links/supports, "
             "then kb triage apply {run}")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
OFFICE_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf"}
# detected kind -> evidence `kind` value
EVIDENCE_KIND = {"image": "screenshot", "pcap": "pcap", "nmap": "scan", "nessus": "scan", "log": "log",
                 "text": "log", "json": "export", "csv": "export"}

# --- extraction regexes (spec §2.10 step 5) ---
RX = {
    "url": re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.I),
    "email": re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"),
    "cve": re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.I),
    "ipv4": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    "ipv6": re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}(?::?[0-9a-fA-F]{1,4}|:)(?![\w:])"),
    "fqdn": re.compile(r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{1,62}\b"),
    "domain_user": re.compile(r"\b([A-Za-z0-9][\w.-]{0,30})\\([A-Za-z0-9][\w.$-]{0,40})"),
    "hyphen_host": re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b"),
}
_TS_NAME = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ T_-]*(?:at[ _])?(\d{2})[-_.:h]?(\d{2})[-_.:m]?(\d{2})")


# --- small helpers ---------------------------------------------------------------

def _runs_dir(vault) -> Path:
    return vault.kb_dir / "triage"


def _run_dir(vault, run: str) -> Path:
    d = _runs_dir(vault) / run
    if not re.fullmatch(r"\d{8}-\d{6}(?:-\d+)?", run) or not (d / "proposals.jsonl").is_file():
        runs = sorted(p.name for p in _runs_dir(vault).glob("*/proposals.jsonl") for p in [p.parent])
        raise KBError(f"no triage run {run!r}; runs: {', '.join(runs) or 'none (run `kb triage scan`)'}")
    return d


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _save_json(path, data) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _git_author(root: Path, path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%an", "--", str(path)],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out or "unknown"


def _wikilink(v: str) -> str:
    v = str(v).strip()
    return v if v.startswith("[[") else f"[[{v}]]"


def _link_id(v: str) -> str:
    return str(v).strip().removeprefix("[[").removesuffix("]]").split("|")[0].split("#")[0]


# --- kind detection ----------------------------------------------------------------

def _head(path, n=4096) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _as_text(head: bytes) -> str | None:
    if b"\x00" in head:
        return None
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start >= len(head) - 4:  # a multibyte char cut at the 4 KB boundary
            return head[:e.start].decode("utf-8", "replace")
        return None


def is_url_list(text: str) -> bool:
    lines = [l.strip().lstrip("-*+ ").strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    return bool(lines) and sum(1 for l in lines if RX["url"].search(l) and len(l) < 300) >= 0.8 * len(lines)


def detect_kind(path) -> str:
    """image | pdf | office | archive | pcap | nmap | nessus | json | csv | markdown | urllist | log | text | binary."""
    path = Path(path)
    ext = "".join(path.suffixes[-2:]).lower() if path.name.lower().endswith(".tar.gz") else path.suffix.lower()
    h = _head(path)
    if (h.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*"))
            or (h[:4] == b"RIFF" and h[8:12] == b"WEBP") or (ext in IMAGE_EXT and h[4:8] == b"ftyp")):
        return "image"
    if h.startswith(b"%PDF"):
        return "pdf"
    if h.startswith(b"PK\x03\x04") or h.startswith(b"\xd0\xcf\x11\xe0"):
        return "office" if ext in OFFICE_EXT else "archive"
    if h[:4] in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d",
                 b"\x0a\x0d\x0d\x0a"):
        return "pcap"
    if h.startswith(b"\x1f\x8b") or h[257:262] == b"ustar" or ext in (".tar", ".tgz", ".tar.gz"):
        return "archive"
    text = _as_text(h)
    if text is None:
        return "office" if ext in OFFICE_EXT else "binary"
    if "<nmaprun" in text:
        return "nmap"
    if "<NessusClientData" in text:
        return "nessus"
    if is_url_list(text):
        return "urllist"
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext == ".json" or text.lstrip()[:1] in ("{", "["):
        return "json"
    if ext in (".csv", ".tsv"):
        return "csv"
    return "log" if ext == ".log" else "text"


# --- archives ----------------------------------------------------------------------

def _safe_member(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or not p.parts or ".." in p.parts or re.match(r"^[A-Za-z]:", name):
        return None
    return p


def unpack(archive: Path, dest: Path, max_bytes: int) -> list:
    """Extract regular files from a zip or tar into dest. Returns [(member_name, path)].
    Refuses path traversal, absolute names and archives larger than max_bytes uncompressed."""
    out, total = [], 0

    def write(name, src, mtime):
        nonlocal total
        rel = _safe_member(name)
        if rel is None:
            raise KBError(f"unsafe member path {name!r}")
        target = dest.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                total += len(chunk)  # counted while reading: header sizes can lie
                if total > max_bytes:
                    raise KBError(f"unpacks to more than {max_bytes >> 20} MB (caps.max_mb x 20)")
                f.write(chunk)
        if mtime:
            os.utime(target, (mtime, mtime))
        out.append((rel.as_posix(), target))

    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:  # skip dirs, symlinks
                        continue
                    with z.open(info) as src:
                        write(info.filename, src, dt.datetime(*info.date_time).timestamp())
        else:
            with tarfile.open(archive) as t:
                for m in t:
                    if m.isfile():
                        write(m.name, t.extractfile(m), m.mtime)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, RuntimeError) as e:
        raise KBError(f"cannot unpack: {e}") from None
    return out


# --- timestamps, OCR, matching -------------------------------------------------------

def captured_time(path: Path, name: str, is_image: bool):
    """(captured, captured_from): EXIF DateTimeOriginal, else a timestamp in the filename, else mtime."""
    if is_image:
        try:
            from PIL import Image
            with Image.open(path) as im:
                exif = im.getexif()
                v = exif.get_ifd(0x8769).get(36867) or exif.get(306)
            if v:
                return dt.datetime.strptime(str(v).strip()[:19], "%Y:%m:%d %H:%M:%S").isoformat(), "exif"
        except Exception:  # Pillow missing or an unreadable image: fall through
            pass
    m = _TS_NAME.search(name)
    if m:
        try:
            return dt.datetime(*map(int, m.groups())).isoformat(), "filename"
        except ValueError:
            pass
    return iso(dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)), "mtime"


def handle_index(vault) -> dict:
    """casefolded id / alias / handle value -> record id."""
    idx = {}
    for r in vault.records:
        for h in r.fm.get("handles") or []:
            h = str(h)
            idx.setdefault(h.casefold(), r.id)
            if ":" in h:
                idx.setdefault(h.split(":", 1)[1].strip().casefold(), r.id)
    for name, r in vault.by_name.items():
        idx.setdefault(name, r.id)
    return idx


def extract_terms(text: str) -> set:
    terms = set()
    for kind, rx in RX.items():
        for m in rx.finditer(text):
            if kind == "domain_user":
                terms.update((m.group(1), m.group(2), m.group(0)))
                continue
            v = m.group(0).rstrip(".,;:")
            terms.add(v)
            if kind == "url":
                host = re.sub(r"^https?://", "", v, flags=re.I).split("/")[0].split(":")[0]
                terms.update((host, host.split(".")[0]))
            elif kind == "fqdn":
                terms.add(v.split(".")[0])  # hostname part of host.domain.tld
            elif kind == "email":
                terms.add(v.split("@", 1)[1])
    return {t.casefold() for t in terms if len(t) >= 3}


def secret_hits(text: str, words: list, extra) -> list:
    """Secret/PII hits without their values; for OCR text, the boxes of the words they overlap."""
    hits = secrets.scan(text, extra)
    spans, pos = [], 0
    for w in words:  # ocr text is the words joined in order, so offsets can be rebuilt
        i = text.find(w["text"], pos)
        if i >= 0:
            spans.append((i, i + len(w["text"]), w))
            pos = i + len(w["text"])
    return [{"kind": h.kind, "category": h.category, "start": h.start, "end": h.end,
             "boxes": [{k: w[k] for k in ("left", "top", "width", "height")}
                       for s, e, w in spans if s < h.end and e > h.start]} for h in hits]


# --- scan ------------------------------------------------------------------------------

def _pick_type(vault, prefer: str, **traits):
    names = [n for n, t in vault.profile.types.items() if all(getattr(t, k) == v for k, v in traits.items())]
    return prefer if prefer in names else (names[0] if names else "")


def _new_run(vault) -> tuple[str, Path]:
    base = now_utc().strftime("%Y%m%d-%H%M%S")
    run, n = base, 2
    while (_runs_dir(vault) / run).exists():
        run, n = f"{base}-{n}", n + 1
    d = _runs_dir(vault) / run
    d.mkdir(parents=True)
    return run, d


def _inbox_files(vault, sub: Path | None = None):
    """(path, contributor, rel) for every inbox file (under sub, if given) except queue files and dotfiles."""
    inbox = vault.root / "inbox"
    if not inbox.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(sub or inbox):
        dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
        d = Path(dirpath)
        for fn in sorted(filenames):
            if fn.startswith(".") or (d == inbox and re.fullmatch(r"discover-.*\.md", fn)):
                continue
            p = d / fn
            parts = p.relative_to(inbox).parts
            who = parts[0] if len(parts) > 1 else _git_author(vault.root, p)
            yield p, who, vault.rel(p)


def _subtree(vault, path: str) -> Path:
    inbox, sub = (vault.root / "inbox").resolve(), (vault.root / path).resolve()
    if not (sub.is_dir() and inbox in sub.parents):
        raise KBError(f"{path} is not a folder under inbox/; pass one like inbox/<name>/<folder> "
                      f"(relative to the vault root; omit it to scan everything)")
    return sub


def scan(vault, context: str | None = None, path: str | None = None) -> dict:
    sub = _subtree(vault, path) if path else None
    prof = vault.profile
    contexts = [c.get("id") if isinstance(c, dict) else c for c in prof.get("contexts") or []]
    if context and contexts and context not in contexts:
        raise KBError(f"unknown context {context!r}; kb.yaml contexts: {', '.join(map(str, contexts))}")
    if not context and len(contexts) == 1:
        context = contexts[0]
    extra = (prof.get("secrets") or {}).get("extra_patterns") or []
    max_bytes = int(float((prof.get("caps") or {}).get("max_mb", 25)) * 20 * (1 << 20))
    ev_type = _pick_type(vault, "evidence", role="input", origin="internal")
    note_type = _pick_type(vault, "topic", role="synthesis", origin="internal", tracked="none")
    src_type = _pick_type(vault, "source", origin="external")
    known = dict(capture._index(vault)["sha"])  # flat sha256 + every capture.yaml main file
    for r in vault.records:  # old nested format, until `kb migrate originals`
        for o in r.fm.get("originals") or []:
            if isinstance(o, dict) and o.get("sha256"):
                known.setdefault(o["sha256"], r.id)
    idx = handle_index(vault)
    run, rdir = _new_run(vault)

    items, archives = [], {}  # items: (path, contributor, obtained_from, display name)
    for p, who, rel in _inbox_files(vault, sub):
        if detect_kind(p) == "archive":
            dest = rdir / "unpacked" / slugify(rel, 200)
            try:
                members = unpack(p, dest, max_bytes)
            except KBError as e:
                shutil.rmtree(dest, ignore_errors=True)
                items.append((p, who, rel, p.name, f"archive could not be unpacked safely ({e}); "
                                                    f"unpack it by hand or exclude it"))
                continue
            archives[rel] = {"unpacked": vault.rel(dest) if members else "", "members": []}
            for name, mp in members:
                archives[rel]["members"].append(f"{rel}:{name}")
                items.append((mp, who, f"{rel}:{name}", name, ""))
        else:
            items.append((p, who, rel, p.name, ""))

    # ids in use: records, aliases and file stems outside the inbox (vault.new_id counts inbox files too)
    taken = set(vault.by_name) | {Path(k).stem.casefold() for k, f in vault.files_by_name.items()
                                  if not vault.rel(f).startswith(("inbox/", ".kb/"))}
    for f in _runs_dir(vault).glob("*/proposals.jsonl"):  # ids proposed by runs not applied yet
        if not _load_json(f.parent / "applied.json", {}).get("applied_at"):
            taken |= {str(json.loads(l).get("id", "")).casefold() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()}
    seqs, seen, proposals = {}, {}, []
    for n, (p, who, origin, name, problem) in enumerate(items, 1):
        sha = sha256_file(p)
        kind = "binary" if problem else detect_kind(p)
        prop = dict.fromkeys(FIELDS, "")
        prop.update(file=vault.rel(p), obtained_from=origin, sha256=sha, contributor=who, context=context or "",
                    suggest_links=[], sensitive=[], links=[], supports=[])
        prop["captured"], prop["captured_from"] = captured_time(p, name, kind == "image")
        text, words = "", []
        if kind == "image" and ocr.available():
            try:
                text, words = ocr.ocr_image(p)
                _save_json(rdir / f"{n:04d}.ocr.json", {"file": prop["file"], "sha256": sha, "text": text,
                                                         "words": words})
            except KBError as e:
                prop["reason"] = f"ocr failed: {e}"
        elif kind not in ("image", "pdf", "office", "pcap", "binary", "archive"):
            text = _as_text(_head(p)) or ""
        prop["ocr_excerpt"] = re.sub(r"\s+", " ", text)[:300] if kind == "image" else ""
        prop["suggest_links"] = sorted({_wikilink(idx[t]) for t in extract_terms(text) if t in idx})
        hits = secret_hits(text, words, extra)
        if hits:
            prop["sensitive"] = sorted({h["category"] for h in hits})
            _save_json(rdir / f"{n:04d}.hits.json", {"file": prop["file"], "sha256": sha, "hits": hits})

        dup = known.get(sha) or seen.get(sha)
        if dup:
            prop.update(route="duplicate", reason=f"duplicate-of {dup}")
        elif problem:
            prop.update(route="hold", question=problem)
        elif kind in EVIDENCE_KIND:
            prop.update(route="evidence", type=ev_type)
            if not ev_type:
                prop.update(route="hold", question="kb.yaml has no internal input type for evidence; add one")
        elif kind in ("pdf", "office"):
            prop.update(route="source", type=src_type)
        elif kind == "urllist":
            prop.update(route="capture", type=src_type,
                        links=list(dict.fromkeys(m.group(0).rstrip(".,;") for m in RX["url"].finditer(text))))
        elif kind == "markdown":
            prop.update(route="note", type=note_type)
        else:
            prop.update(route="hold", question=f"unrecognized {kind} file; route it (evidence, source, exclude)")
        seen.setdefault(sha, origin)

        if prop["route"] in ("evidence", "note"):
            if prop["route"] == "evidence" and context:
                base = f"{context}-{slugify(who)}"
                if base not in seqs:
                    rx = re.compile(re.escape(base.casefold()) + r"-(\d{4,})$")
                    seqs[base] = max([int(m.group(1)) for k in taken if (m := rx.match(k))], default=0)
                seqs[base] += 1
                while (rid := f"{base}-{seqs[base]:04d}").casefold() in taken:
                    seqs[base] += 1
            else:
                rid = stem = slugify(Path(name).stem)
                k = 2
                while rid.casefold() in taken:
                    rid, k = f"{stem}-{k}", k + 1
            taken.add(rid.casefold())
            prop["id"] = rid
        proposals.append(prop)

    atomic_write(rdir / "proposals.jsonl", "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in proposals))
    _save_json(rdir / "archives.json", archives)
    return {"run": run, "proposals": vault.rel(rdir / "proposals.jsonl"), "files": len(proposals),
            "routes": dict(Counter(p["route"] for p in proposals)),
            "duplicates": [{"file": p["obtained_from"], "of": p["reason"].split(" ", 1)[1]}
                           for p in proposals if p["route"] == "duplicate"],
            "secrets": [{"file": p["obtained_from"], "kinds": sorted({h["kind"] for h in _load_json(
                rdir / f"{i:04d}.hits.json", {}).get("hits", [])})}
                for i, p in enumerate(proposals, 1) if p["sensitive"]],
            "archives": len(archives), "next": NEXT_STEP.format(run=run)}


def _scan_text(r: dict) -> list:
    lines = [f"triage run {r['run']}: {r['files']} file(s) -> {r['proposals']}"]
    lines += [f"  {route}: {n}" for route, n in sorted(r["routes"].items())]
    lines += [f"  duplicate: {d['file']} = {d['of']}" for d in r["duplicates"]]
    lines += [f"  secrets: {s['file']} ({', '.join(s['kinds'])})" for s in r["secrets"]]
    return lines + [r["next"]]


def cmd_scan(args) -> int:
    v = open_from_args(args)
    r = scan(v, args.context, args.path)
    emit(args, r, _scan_text)
    return 0


# --- apply -------------------------------------------------------------------------------

def _key(p) -> str:
    return p.get("obtained_from") or p["file"]


def _validate(vault, props) -> list:
    errs = []
    for i, p in enumerate(props, 1):
        where = f"line {i} ({_key(p)})"
        route = p.get("route")
        if route not in ROUTES:
            errs.append(f"{where}: route {route!r} is not one of {', '.join(ROUTES)}")
            continue
        t = vault.profile.types.get(p.get("type") or "")
        if p.get("type") and t is None:
            errs.append(f"{where}: unknown type {p['type']!r}")
        errs += _check_set(vault, where, route, t or vault.profile.types.get(p.get("type") or "source"), p.get("set"))
        if route == "evidence":
            if not str(p.get("title", "")).strip() or not str(p.get("description", "")).strip():
                errs.append(f"{where}: evidence needs a title and a description (view the file first)")
            if not p.get("id"):
                errs.append(f"{where}: evidence needs an id")
            if t is None:
                errs.append(f"{where}: evidence needs a type from kb.yaml")
            elif "{context}" in t.folder and not p.get("context"):
                errs.append(f"{where}: set context (type {t.name} files evidence by context)")
            else:
                try:
                    t.folder_for(context=p.get("context") or "", kind="file")
                except KBError as e:
                    errs.append(f"{where}: {e}")
        if route == "hold" and not str(p.get("question", "")).strip():
            errs.append(f"{where}: hold needs a question for the user")
        if route == "exclude" and not str(p.get("reason", "")).strip():
            errs.append(f"{where}: exclude needs a reason")
        if route == "entity-update" and not p.get("links"):
            errs.append(f"{where}: entity-update needs the updated entity in links")
    return errs


def _check_set(vault, where, route, t, keys) -> list:
    """Errors for a proposal's `set` (extra front-matter keys written onto the record)."""
    if not keys:
        return []
    if not isinstance(keys, dict):
        return [f"{where}: set must be an object like {{\"tier\": \"primary\"}}"]
    if route not in ("evidence", "note", "source", "capture"):
        return [f"{where}: set has no effect on route {route!r}; it applies to evidence, note, source and capture"]
    prof = vault.profile
    specs = {**(prof.get("scope_fields") or {}), **(t.keys if t else {}),
             "title": "string", "description": "string", "tags": prof.tags if prof.tags is not None else "list"}
    if t and t.origin == "external" and prof.tiers:
        specs["tier"] = prof.tiers
    errs = []
    for k, v in keys.items():
        if k not in specs:
            errs.append(f"{where}: set key {k!r} is not a key of type {t.name if t else '?'}, a scope field, "
                        f"title, description, tags or tier; allowed: {', '.join(sorted(specs))}")
        elif msg := check_value(specs[k], v):
            errs.append(f"{where}: set {k}: {msg}")
    return errs


def _retitle(body: str, title) -> str:
    """Rewrite the first H1 to the title (lint wants them equal)."""
    return re.sub(r"(?m)^#[ \t]+.+$", lambda _: f"# {title}", body, count=1)


def _set_keys(rec, keys) -> None:
    if keys:
        rec.fm.update(keys)
        if "title" in keys:
            rec.body = _retitle(rec.body, keys["title"])
        rec.save()


def _copy_in(src: Path, dest: Path, sha: str) -> None:
    """Copy src to dest atomically; never overwrite a different existing file."""
    if dest.exists():
        if sha256_file(dest) == sha:
            return
        raise KBError(f"{dest} already exists with different content; give the proposal another id")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def _ocr_texts(rdir: Path) -> dict:
    return {d.get("sha256"): d.get("text", "") for f in rdir.glob("*.ocr.json") for d in [_load_json(f, {})]}


def _apply_evidence(vault, p, ocr_text: str) -> str:
    t = vault.profile.type(p["type"])
    src = vault.root / p["file"]
    ext = Path(p["file"]).suffix.lower()
    kind = detect_kind(src) if src.exists() else ""
    folder = vault.root / t.folder_for(context=p.get("context") or "", kind=EVIDENCE_KIND.get(kind, "file"))
    rid, name = p["id"], f"{p['id']}{ext}"
    dest = folder / name
    if src.exists():
        _copy_in(src, dest, p["sha256"])
    elif not dest.exists():
        raise KBError(f"{p['file']} is gone and {vault.rel(dest)} was never written")
    kinds = t.keys.get("kind")
    ev_kind = EVIDENCE_KIND.get(kind, "file")
    if isinstance(kinds, list) and ev_kind not in kinds:
        ev_kind = "file" if "file" in kinds else kinds[0]
    hosts, entities = [], []
    for l in p.get("links") or []:
        r = vault.get(_link_id(l))
        (hosts if r is not None and r.fm.get("kind") == "host" else entities).append(_wikilink(l))
    fm = {"type": t.name, "title": p["title"].strip(), "description": p["description"].strip(), "kind": ev_kind,
          "context": p.get("context") or None, "contributor": [p["contributor"]],
          "captured": p.get("captured") or None, "captured_from": p.get("captured_from") or None,
          "obtained_from": p.get("obtained_from") or None, "phase": p.get("phase") or None,
          "file": f"[[{name}]]",
          "original": vault.rel(dest), "sha256": p["sha256"], "bytes": dest.stat().st_size,
          "format": ext.lstrip(".") or "bin",
          "hosts": hosts or None, "entities": entities or None,
          "supports": [_wikilink(s) for s in p.get("supports") or []] or None,
          "sensitive": p.get("sensitive") or None, "updated": dt.date.fromisoformat(today())}
    if kind == "image":
        fm["ocr_quality"] = "good" if len(ocr_text.split()) >= 5 else ("partial" if ocr_text.strip() else "none")
    fm.update(p.get("set") or {})
    fm = {k: v for k, v in fm.items() if v is not None}
    body = f"\n# {fm['title']}\n\n![[{name}]]\n\n{fm['description']}\n"
    if ocr_text.strip():
        body += "\n> [!quote]- OCR\n" + "".join(f"> {l}".rstrip() + "\n" for l in ocr_text.strip().splitlines())
    body += "\n## Contributor notes\n"
    sidecar = folder / f"{rid}.md"
    if not sidecar.exists():
        write_record(sidecar, fm, body)
    return rid


def _apply_note(vault, p) -> str:
    t = vault.profile.type(p.get("type") or _pick_type(vault, "topic", role="synthesis", origin="internal", tracked="none"))
    src = vault.root / p["file"]
    rid = p.get("id") or vault.new_id(slugify(src.stem))
    dest = vault.root / t.folder_for() / f"{rid}.md"
    if not src.exists() and dest.exists():
        return rid
    raw = src.read_text(encoding="utf-8", errors="replace")
    try:
        fm, body = split_front_matter(raw)
    except KBError:
        fm, body = None, raw
    fm = dict(fm or {})
    h1 = re.search(r"(?m)^#\s+(.+)$", body)
    for k, v in (("type", t.name), ("title", p.get("title") or (h1.group(1).strip() if h1 else src.stem)),
                 ("description", p.get("description")), ("contributor", [p["contributor"]])):
        if v and not fm.get(k):
            fm[k] = v
    fm.update(p.get("set") or {})
    if dest.exists():
        raise KBError(f"{vault.rel(dest)} already exists; give the proposal another id")
    if h1:
        body = _retitle(body, fm["title"])
    atomic_write(dest, dump_front_matter(fm) + (body if body.startswith("\n") else "\n" + body))
    return rid


def _capture_mod():
    try:
        return importlib.import_module("kb.capture")
    except ImportError:
        return None


def apply(vault, run: str) -> dict:
    rdir = _run_dir(vault, run)
    props = [json.loads(l) for l in (rdir / "proposals.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    state = _load_json(rdir / "applied.json", {"done": {}, "urls": {}})
    done, urls = state.setdefault("done", {}), state.setdefault("urls", {})
    errs = _validate(vault, [p for p in props if _key(p) not in done])
    if errs:
        raise KBError(f"{len(errs)} proposal(s) need fixing in {vault.rel(rdir / 'proposals.jsonl')}:\n  "
                      + "\n  ".join(errs))
    ocr_by_sha = _ocr_texts(rdir)
    held = {}

    def finish(p, disposition, target):
        src = vault.root / p["file"]
        if src.exists():
            src.unlink()
        done[_key(p)] = {"sha256": p["sha256"], "disposition": disposition, "target": target}
        _save_json(rdir / "applied.json", state)

    for p in props:
        key = _key(p)
        if key in done:
            continue
        route = p["route"]
        if route == "hold":
            held[key] = p["question"]
        elif route == "evidence":
            finish(p, "routed", _apply_evidence(vault, p, ocr_by_sha.get(p["sha256"], "")))
        elif route == "note":
            finish(p, "routed", _apply_note(vault, p))
        elif route == "entity-update":
            finish(p, "routed", _link_id(p["links"][0]))
        elif route == "duplicate":
            finish(p, "duplicate-of", p.get("reason", "").removeprefix("duplicate-of ").strip())
        elif route == "exclude":
            finish(p, "excluded", p["reason"])
        elif route in ("source", "capture"):
            cap = _capture_mod()
            if cap is None:
                held[key] = "kb capture not available"
                continue
            try:
                if route == "source":
                    rec = cap.capture_file(vault, vault.root / p["file"], id=p.get("id") or None,
                                           type=p.get("type") or "source", obtained_from=key)
                    _set_keys(rec, p.get("set"))
                    finish(p, "routed", rec.id)
                    continue
                failed = []
                for url in p.get("links") or []:
                    if f"{key}|{url}" in urls:
                        continue
                    try:
                        rec = cap.capture_url(vault, url, type=p.get("type") or "source", obtained_from=key)
                        _set_keys(rec, p.get("set"))
                        urls[f"{key}|{url}"] = rec.id
                        _save_json(rdir / "applied.json", state)
                    except KBError as e:
                        failed.append(f"{url}: {e}")
                if failed:
                    held[key] = "capture failed: " + "; ".join(failed)
                else:
                    finish(p, "routed", " ".join(v for k, v in urls.items() if k.startswith(key + "|")))
            except KBError as e:
                held[key] = f"capture failed: {e}"
        vault.reload()

    archives = _load_json(rdir / "archives.json", {})
    rows = []
    by_key = {_key(p): p for p in props}
    for arch, info in archives.items():
        if all(m in done for m in info["members"]):
            (vault.root / arch).unlink(missing_ok=True)
        sha = done.get(arch, {}).get("sha256") or (sha256_file(vault.root / arch) if (vault.root / arch).exists() else "")
        done.setdefault(arch, {"sha256": sha, "disposition": "extracted-from-archive", "target": info["unpacked"]})
    for key, d in done.items():
        rows.append((key, d["sha256"], d["disposition"], d["target"]))
    rows += [(k, by_key[k]["sha256"], "held", q) for k, q in held.items()]
    state["held"], state["applied_at"] = held, iso(now_utc())
    _save_json(rdir / "applied.json", state)
    clean = lambda s: re.sub(r"[\t\r\n]+", " ", str(s))
    manifest = vault.root / "intake" / f"{run[:4]}-{run[4:6]}-{run[6:8]}-{run}.tsv"
    atomic_write(manifest, "file\tsha256\tdisposition\ttarget\n"
                 + "".join("\t".join(map(clean, r)) + "\n" for r in rows))
    counts = Counter(r[2] for r in rows)
    append_log(vault.root, "triage", run, ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    return {"run": run, "manifest": vault.rel(manifest), "counts": dict(counts),
            "held": [{"file": k, "question": q} for k, q in held.items()]}


def cmd_apply(args) -> int:
    v = open_from_args(args)
    r = apply(v, args.run)
    emit(args, r, lambda r: [f"applied {r['run']} -> {r['manifest']}"]
         + [f"  {k}: {n}" for k, n in sorted(r["counts"].items())]
         + [f"  held: {h['file']} — {h['question']}" for h in r["held"]])
    return 0


# --- status --------------------------------------------------------------------------------

def status(vault) -> dict:
    pending, held = [], []
    for d in sorted(_runs_dir(vault).glob("*/proposals.jsonl")):
        state = _load_json(d.parent / "applied.json", {})
        if not state.get("applied_at"):
            pending.append(d.parent.name)
        for k, q in (state.get("held") or {}).items():
            if (vault.root / k.split(":", 1)[0]).exists():
                held.append({"run": d.parent.name, "file": k, "question": q})
    inbox = vault.root / "inbox"
    per = Counter()
    if inbox.is_dir():
        for p in inbox.rglob("*"):
            parts = p.relative_to(inbox).parts
            if p.is_file() and not any(x.startswith(".") for x in parts) \
                    and not (len(parts) == 1 and re.fullmatch(r"discover-.*\.md", p.name)):
                per[parts[0] if len(parts) > 1 else "(inbox root)"] += 1
    return {"pending_runs": pending, "inbox": dict(sorted(per.items())), "held": held}


def cmd_status(args) -> int:
    v = open_from_args(args)
    r = status(v)
    emit(args, r, lambda r: [f"pending runs: {', '.join(r['pending_runs']) or 'none'}",
                             "inbox: " + (", ".join(f"{k} {n}" for k, n in r["inbox"].items()) or "empty")]
         + [f"held ({h['run']}): {h['file']} — {h['question']}" for h in r["held"]])
    return 0


def register(subparsers, common) -> None:
    p = subparsers.add_parser("triage", parents=[common], help="route inbox files: scan, review, apply")
    sub = p.add_subparsers(dest="triage_command", metavar="SUBCOMMAND")
    s = sub.add_parser("scan", parents=[common], help="propose a route for every inbox file")
    s.add_argument("path", nargs="?", metavar="PATH", help="only scan this folder under inbox/ (e.g. inbox/alice/batch-3)")
    s.add_argument("--context", help="profile context for evidence ids and folders")
    s.set_defaults(func=cmd_scan)
    a = sub.add_parser("apply", parents=[common], help="execute a reviewed run's proposals")
    a.add_argument("run", help="run id from `kb triage scan` (YYYYMMDD-HHMMSS)")
    a.set_defaults(func=cmd_apply)
    st = sub.add_parser("status", parents=[common], help="pending runs, inbox counts, held items")
    st.set_defaults(func=cmd_status)
    p.set_defaults(func=lambda args: (p.print_help(), 0)[1])
