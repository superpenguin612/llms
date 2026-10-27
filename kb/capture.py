"""kb capture URL|PATH: save an immutable original, extract text with anchors, write or update a stub card.

Python API (used by triage, collect and refresh):
    capture_url(vault, url, id=None, type="source", kind=None, obtained_from=None) -> Record
    capture_file(vault, path, id=None, type="source", kind=None, obtained_from=None) -> Record
"""

import datetime as dt
import difflib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from kb import __version__, extract, inject, vault as vaultmod
from kb.records import (Record, append_to_section, dump_front_matter, find_section, parse_excerpts,
                        split_front_matter, write_record)
from kb.util import (KBError, append_log, atomic_write, emit, excerpt_in_text, iso, normalize, now_utc,
                     sha256_bytes, sha256_file, slugify, today)

UA = f"Mozilla/5.0 (compatible; kb/{__version__}; knowledge-base capture)"
MB = 1 << 20
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")
_REPO = re.compile(r"^https?://(?:www\.)?(github\.com|gitlab\.com|codeberg\.org)/([^/\s?#]+)/([^/\s?#]+?)(?:\.git)?/?$")
CARD_KIND = {"html": "webpage", "pdf": "pdf", "image": "image", "subtitles": "video", "code": "code", "text": "docs",
             ".pptx": "slides", ".docx": "office-doc", ".xlsx": "spreadsheet", ".xls": "spreadsheet"}
# yt-dlp info.json keys holding per-request media URLs (they embed the fetching IP); not part of the work
_INFO_DROP = ("formats", "requested_formats", "requested_downloads", "requested_subtitles", "automatic_captions",
              "subtitles", "thumbnails", "http_headers", "url", "manifest_url")


# --- HTTP ---------------------------------------------------------------------------

def http_get(url, limit=None, headers=None, timeout=30):
    """GET with redirects followed. Returns status, url (final), headers (list of pairs), ctype, body.
    HTTP errors come back as a status; network errors raise OSError (URLError)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        body = r.read(limit + 1) if limit else r.read()
        if limit and len(body) > limit:
            raise KBError(f"{url} is over caps.max_mb ({limit // MB} MB); raise caps.max_mb in kb.yaml "
                          f"or keep the file outside the vault")
        hdrs = r.headers
        return SimpleNamespace(status=getattr(r, "status", None) or r.getcode(), url=r.geturl() or url,
                               headers=list(hdrs.items()) if hdrs else [],
                               ctype=hdrs.get_content_type() if hdrs else "", body=body)


def wayback_snapshot(url) -> str | None:
    """Newest existing Wayback snapshot (raw `id_` form), read only; never submits anything."""
    try:
        r = http_get("https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe=""))
        snap = json.loads(r.body or b"{}").get("archived_snapshots", {}).get("closest") or {}
    except (OSError, ValueError):
        return None
    if not snap.get("available") or not str(snap.get("status", "200")).startswith("2") or not snap.get("url"):
        return None
    return re.sub(r"/web/(\d+)/", r"/web/\1id_/", snap["url"], count=1)


def _ext(resp) -> str:
    known = {"text/html": ".html", "application/xhtml+xml": ".html", "application/pdf": ".pdf",
             "text/plain": ".txt", "text/vtt": ".vtt"}
    suffix = Path(urllib.parse.urlparse(resp.url).path).suffix.lower()
    if resp.ctype in known:
        return known[resp.ctype]
    if suffix and resp.ctype in ("", "application/octet-stream", "binary/octet-stream"):
        return suffix
    return mimetypes.guess_extension(resp.ctype or "") or suffix or ".bin"


def _fetch_web(vault, url, stage):
    limit = _caps(vault)[1]
    live, err = None, ""
    try:
        r = http_get(url, limit)
        live = r.status
    except OSError as e:
        r, err = None, str(getattr(e, "reason", e))
    via = "urllib"
    if r is None or r.status >= 400:
        snap = wayback_snapshot(url)
        why = f"HTTP {live}" if live else f"network error: {err}"
        if not snap:
            raise KBError(f"{url} is dead ({why}) and has no Wayback snapshot; capture a copy by hand "
                          f"(kb capture FILE --id ...) or record originals: [] with an originals_note")
        r = http_get(snap, limit)
        if r.status >= 400:
            raise KBError(f"{url} is dead ({why}) and its Wayback snapshot returned HTTP {r.status}")
        via = f"wayback (live: {why})"
    ext = _ext(r)
    base = Path(urllib.parse.urlparse(r.url).path).name
    name = base if Path(base).suffix.lower() == ext and re.fullmatch(r"[\w.-]+", base) else f"index{ext}"
    (stage / name).write_bytes(r.body)
    (stage / "headers.txt").write_text(f"HTTP {r.status}\nURL: {r.url}\n" +
                                       "".join(f"{k}: {v}\n" for k, v in r.headers), encoding="utf-8")
    final = url if via != "urllib" else r.url
    return SimpleNamespace(main=name, fetched_url=r.url, http_status=r.status, via=via, final_url=final,
                           dead=via != "urllib", text=None, meta=None, title=None, kind=None)


# --- subprocess routes (video, repo) --------------------------------------------------

def _run(cmd, cwd=None, timeout=600) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True).stdout
    except FileNotFoundError:
        hint = {"yt-dlp": "pip install yt-dlp", "git": "apt install git"}.get(cmd[0], f"install {cmd[0]}")
        raise KBError(f"{cmd[0]} not found; install it: {hint}") from None
    except subprocess.CalledProcessError as e:
        last = (e.stderr or "").strip().splitlines()[-1:] or [f"exit {e.returncode}"]
        raise KBError(f"{cmd[0]} failed: {last[0]}") from None
    except subprocess.TimeoutExpired:
        raise KBError(f"{cmd[0]} timed out after {timeout}s") from None


def _fetch_video(vault, url, stage):
    _run(["yt-dlp", "--skip-download", "--write-info-json", "--write-subs", "--write-auto-subs",
          "--sub-langs", "en.*,en", "--sub-format", "vtt", "--no-playlist", "-o", str(stage / "video.%(ext)s"), url])
    info_path = next(iter(sorted(stage.glob("*.info.json"))), None)
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path else {}
    if info_path:
        info_path.write_text(json.dumps({k: v for k, v in info.items() if k not in _INFO_DROP}, indent=1,
                                        ensure_ascii=False), encoding="utf-8")
    subs = sorted(stage.glob("*.vtt"), key=lambda p: (not p.name.endswith(".en.vtt"), p.name))
    if subs:
        text, meta = extract.extract(subs[0], "subtitles")
    else:
        text, meta = "", {"anchors": "none", "text_quality": "none",
                          "note": "no captions found; transcribe locally (e.g. faster-whisper) and recapture"}
    meta["extracted_with"] = extract._version("yt-dlp") + (" + kb subtitles" if subs else "")
    if info.get("upload_date"):
        d = str(info["upload_date"])
        meta["issued"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if info.get("uploader") or info.get("channel"):
        meta["author"] = [info.get("uploader") or info.get("channel")]
    main = info_path.name if info_path else (subs[0].name if subs else None)
    if main is None:
        raise KBError(f"yt-dlp saved nothing for {url}; check the URL or update yt-dlp (pip install -U yt-dlp)")
    final = info.get("webpage_url") or url
    return SimpleNamespace(main=main, fetched_url=final, http_status=None, via="yt-dlp", final_url=final,
                           dead=False, text=text, meta=meta, title=info.get("title"), kind="video")


def _repo_parts(url):
    m = _REPO.match(url)
    if m:
        return m.group(2), m.group(3)
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        raise KBError(f"can't tell owner/repo from {url}; pass the clone URL")
    return parts[-2], re.sub(r"\.git$", "", parts[-1])


def _fetch_repo(vault, url, stage):
    root_cfg = vault.profile.get("clone_root")
    if not root_cfg:
        raise KBError("capturing a repo needs clone_root in kb.yaml (a folder outside the vault for clones), "
                      "e.g. clone_root: ~/src/clones")
    owner, name = _repo_parts(url)
    shown = f"{str(root_cfg).rstrip('/')}/{owner}/{name}"
    path = vaultmod.expand(shown)
    if (path / ".git").exists():
        _run(["git", "-C", str(path), "pull", "--ff-only", "--quiet"])
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", url, str(path)])
    commit = _run(["git", "-C", str(path), "rev-parse", "HEAD"]).strip()
    (stage / "tree.txt").write_text(_run(["git", "-C", str(path), "ls-files"]), encoding="utf-8")
    (stage / "commit.txt").write_text(_run(["git", "-C", str(path), "log", "-1"]), encoding="utf-8")
    readme = next(iter(sorted(p for p in path.glob("README*") if p.is_file())), None)
    readme_text = ""
    if readme:
        shutil.copy2(readme, stage / readme.name)
        readme_text = readme.read_text(encoding="utf-8", errors="replace")
    version = _run(["git", "--version"]).strip().replace("git version ", "git ")
    canonical = url if _REPO.match(url) is None else re.sub(r"(\.git)?/?$", "", url)
    return SimpleNamespace(main="commit.txt", fetched_url=url, http_status=None, via="git", final_url=canonical,
                           dead=False, text=readme_text, title=f"{owner}/{name}", kind="repo",
                           meta={"extracted_with": version, "clone": f"clone:{shown}@{commit}", "commit": commit})


def _route(url, kind):
    host = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    if kind == "repo" or (kind is None and _REPO.match(url)):
        return _fetch_repo
    if kind in ("video", "podcast") or (kind is None and any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)):
        return _fetch_video
    return _fetch_web


# --- helpers ------------------------------------------------------------------------

def _caps(vault):
    caps = vault.profile.get("caps") or {}
    return float(caps.get("warn_mb", 10)) * MB, int(float(caps.get("max_mb", 25)) * MB)


def _date_dir(base: Path) -> Path:
    d, n = today(), 2
    p = base / d
    while p.exists():
        p, n = base / f"{d}-{n}", n + 1
    return p


def _format(name: str) -> str:
    if name == "headers.txt":
        return "headers"
    if name.endswith(".info.json"):
        return "info-json"
    return Path(name).suffix.lstrip(".").lower() or "bin"


def _read_text_file(path: Path):
    if not path.is_file():
        return None, ""
    fm, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
    return fm or {}, body


def mark_gone(body: str, eids, date: str) -> str:
    """Add `Gone: DATE` to each listed excerpt block."""
    sec = find_section(body, "Excerpts")
    if sec is None:
        return body
    _, cs, ce = sec
    section = body[cs:ce]
    nxt_rx = re.compile(r"(?m)^###[ \t]+e\d+[ \t]*$")
    for eid in eids:
        m = re.search(rf"(?m)^###[ \t]+{re.escape(eid)}[ \t]*$", section)
        if not m:
            continue
        n = nxt_rx.search(section, m.end())
        end = n.start() if n else len(section)
        chunk = section[m.end():end]
        core = chunk.rstrip("\n")
        section = section[:m.end()] + core + f"\nGone: {date}" + (chunk[len(core):] or "\n") + section[end:]
    return body[:cs] + section + body[ce:]


def add_change(body: str, line: str) -> str:
    """Append a line to ## Changes, creating it just before ## Notes when missing."""
    notes = find_section(body, "Notes")
    if find_section(body, "Changes") is None and notes:
        return body[:notes[0]] + f"## Changes\n{line}\n\n" + body[notes[0]:]
    return append_to_section(body, "Changes", line)


def _day(s=None):
    """A date object (YAML writes it unquoted), or the string when it isn't YYYY-MM-DD."""
    s = s or today()
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return s


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


# --- the capture ----------------------------------------------------------------------

def capture_url(vault, url, id=None, type="source", kind=None, obtained_from=None) -> Record:
    return _capture(vault, url, True, id, type, kind, obtained_from)[0]


def capture_file(vault, path, id=None, type="source", kind=None, obtained_from=None) -> Record:
    return _capture(vault, path, False, id, type, kind, obtained_from)[0]


def _capture(vault, src, is_url, id=None, type="source", kind=None, obtained_from=None):
    """Returns (record, change). change: {id, card, status: new|unchanged|same-text|changed, capture,
    added, removed, gone, dead, warnings}."""
    vault.reload()
    td = vault.profile.type(type)
    existing = vault.get(id) if id else None
    if existing is None and is_url:
        existing = vault.find_by_url(src)
    warn_b, max_b = _caps(vault)
    vault.kb_dir.joinpath("tmp").mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=vault.kb_dir / "tmp", prefix="capture-"))
    warnings = []
    try:
        if is_url:
            got = _route(src, kind or (existing.fm.get("kind") if existing else None))(vault, src, stage)
        else:
            p = Path(src)
            if not p.is_file():
                raise KBError(f"no file {src}")
            if p.stat().st_size > max_b:
                raise KBError(f"{p.name} is {p.stat().st_size / MB:.1f} MB, over caps.max_mb ({max_b // MB}); "
                              f"raise caps.max_mb in kb.yaml or keep it outside the vault")
            shutil.copy2(p, stage / p.name)
            got = SimpleNamespace(main=p.name, fetched_url=None, http_status=None, via="file", final_url=None,
                                  dead=False, text=None, meta=None, title=None, kind=None)
        if existing is None and got.final_url:
            existing = vault.find_by_url(got.final_url)
        main_sha = sha256_file(stage / got.main)
        if existing is None:
            existing = vault.find_by_sha(main_sha)
        if existing is not None:
            td = vault.typedef(existing) or td
            olds = [o for o in existing.fm.get("originals") or [] if isinstance(o, dict)]
            if any(o.get("sha256") == main_sha for o in olds):
                return _touch(existing, td, warnings)
        size = sum(f.stat().st_size for f in stage.iterdir())
        if size > warn_b:
            warnings.append(f"capture is {size / MB:.1f} MB, over caps.warn_mb")

        if got.text is None:
            text, meta = extract.extract(stage / got.main, kind if kind in extract.KINDS else None)
            ek = extract.detect(stage / got.main)
        else:
            text, meta, ek = got.text, got.meta, None
        title = (existing.title if existing else None) or got.title or meta.get("title") or \
            (urllib.parse.urlparse(src).hostname if is_url else Path(src).stem)
        card_kind = kind or got.kind or CARD_KIND.get(Path(got.main).suffix.lower()) or CARD_KIND.get(ek)
        rid = existing.id if existing else (id or vault.new_id(slugify(title)))

        final_dir = _date_dir(vault.root / "originals" / rid)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final_dir)
        captured = iso(now_utc())
        originals = [_drop_none({"path": vault.rel(f), "sha256": sha256_file(f), "bytes": f.stat().st_size,
                                 "format": _format(f.name), "captured": captured,
                                 "fetched_url": got.fetched_url if f.name == got.main else None,
                                 "http_status": got.http_status if f.name == got.main else None,
                                 "via": got.via, "obtained_from": obtained_from})
                     for f in sorted(final_dir.iterdir(), key=lambda f: (f.name != got.main, f.name))]
        flags = inject.scan(text)
        args = SimpleNamespace(vault=vault, rid=rid, td=td, got=got, text=text, meta=meta, title=title,
                               kind=card_kind, src=src if is_url else None, originals=originals, flags=flags,
                               capture=vault.rel(final_dir), warnings=warnings)
        rec, change = _update(args, existing) if existing else _create(args)
        append_log(vault.root, "capture", rid, f"{change['status']} {src if is_url else Path(src).name}")
        vault.reload()
        return rec, change
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _write_text(a, path: Path):
    tfm = {"id": a.rid, "extracted_with": a.meta.get("extracted_with"), "text_quality": a.meta.get("text_quality"),
           "anchors": a.meta.get("anchors"), "text_sha256": sha256_bytes(a.text.encode("utf-8")),
           "capture": a.capture}
    atomic_write(path, dump_front_matter(_drop_none(tfm)) + a.text)
    return tfm["text_sha256"]


def _text_fields(a, fm):
    if "clone" in a.meta:
        fm["text"] = a.meta["clone"]
        fm.pop("text_sha256", None)
    else:
        rel = fm.get("text") if str(fm.get("text", "")).startswith("text/") else f"text/{a.rid}.md"
        fm["text"] = rel
        fm["text_sha256"] = _write_text(a, a.vault.root / rel)
        fm["text_quality"] = a.meta.get("text_quality")
    fm["extracted_with"] = a.meta.get("extracted_with")
    if a.flags:
        fm["injection_flag"] = True
        fm["injection_note"] = " | ".join(a.flags)


def _create(a):
    m, fm = a.meta, {"type": a.td.name, "title": a.title, "kind": a.kind}
    if a.src:
        fm["url"] = a.got.final_url or a.src
        if fm["url"] != a.src:
            fm["given_url"] = a.src
    fm.update(_drop_none({"author": m.get("author"), "issued": _day(m["issued"]) if m.get("issued") else None,
                         "accessed": _day()}))
    fm["originals"] = a.originals
    _text_fields(a, fm)
    if a.td.tracked == "refresh":
        fm["last_checked"] = _day()
    if a.kind == "image":
        fm["file"] = f"[[{Path(a.got.main).name}]]"
    fm = _drop_none(fm)
    hints = [k for k in ("title", "issued", "author") if k in m or (k == "title" and a.got.title)]
    notes = [f"- Stub written by kb capture on {today()}."
             + (f" {', '.join(hints)} came from {m.get('extracted_with', 'the tool')}: hints to confirm." if hints else "")]
    if m.get("note"):
        notes.append(f"- {m['note']}.")
    if m.get("needs_ocr"):
        notes.append(f"- Pages with almost no text layer: {m['needs_ocr']} (OCR'd: {m.get('ocr_pages') or 'none'}).")
    if a.got.dead:
        notes.append(f"- Live page was dead; original is the Wayback snapshot {a.got.fetched_url}.")
    body = f"\n# {a.title}\n\n## Summary\n\n## Key facts\n\n## Excerpts\n\n## Notes\n" + "\n".join(notes) + "\n"
    path = a.vault.root / a.td.folder_for(fm) / f"{a.rid}.md"
    rec = write_record(path, fm, body)
    return rec, _change(a, "new", rec)


def _update(a, rec):
    fm, body, d = rec.fm, rec.body, today()
    change = _change(a, "same-text", rec)
    fm.setdefault("originals", []).extend(a.originals)
    fm["accessed"] = fm["last_checked"] = _day()
    if "clone" in a.meta:
        if fm.get("text") != a.meta["clone"]:
            change["status"] = "changed"
            # shortcut: repo excerpts (file L.. @ commit) are not re-checked for Gone; add when repo excerpts are linted
            body = add_change(body, f"- {d}: recaptured; {fm.get('text')} -> {a.meta['clone']}")
        _text_fields(a, fm)
    else:
        rel = fm.get("text") if str(fm.get("text", "")).startswith("text/") else f"text/{a.rid}.md"
        tpath = a.vault.root / rel
        old_fm, old = _read_text_file(tpath)
        if old_fm is None or normalize(old) != normalize(a.text):
            change["status"] = "changed"
            hist = None
            if old_fm is not None:
                stamp = Path(str(old_fm.get("capture") or "")).name or d
                hist = _date_dir_named(a.vault.root / "text" / "history" / a.rid, stamp)
                hist.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tpath, hist)
            _text_fields(a, fm)
            diff = list(difflib.unified_diff(old.splitlines(), a.text.splitlines(), lineterm="", n=0))
            change["added"] = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            change["removed"] = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            nt = normalize(a.text)
            gone = [e.eid for e in parse_excerpts(body) if not e.withdrawn and not e.gone
                    and excerpt_in_text(e.text, a.text, nt) == "missing"]
            change["gone"] = gone
            body = mark_gone(body, gone, d)
            line = f"- {d}: recaptured; text changed (+{change['added']}/−{change['removed']} lines)"
            line += (f"; gone: {', '.join(gone)}" if gone else "") + (f"; previous text: {a.vault.rel(hist)}" if hist else "")
            body = add_change(body, line)
        elif a.flags:
            fm["injection_flag"], fm["injection_note"] = True, " | ".join(a.flags)
    if a.got.dead:
        fm["status"] = "stale"
        body = add_change(body, f"- {d}: live page dead ({a.got.via.removeprefix('wayback (live: ').rstrip(')')}); "
                                f"captured the Wayback snapshot")
    rec.body = body
    rec.save()
    return rec, change


def _date_dir_named(base: Path, stamp: str) -> Path:
    p, n = base / f"{stamp}.md", 2
    while p.exists():
        p, n = base / f"{stamp}-{n}.md", n + 1
    return p


def _touch(rec, td, warnings):
    """Same bytes as an existing original: only bump last_checked."""
    if td.tracked == "refresh" or "last_checked" in rec.fm:
        rec.fm["last_checked"] = _day()
        rec.save()
    return rec, {"id": rec.id, "card": rec.rel, "status": "unchanged", "capture": None, "added": 0, "removed": 0,
                 "gone": [], "dead": False, "warnings": warnings}


def _change(a, status, rec):
    return {"id": a.rid, "card": a.vault.rel(rec.path), "status": status, "capture": a.capture, "added": 0, "removed": 0,
            "gone": [], "dead": a.got.dead, "injection_flag": bool(a.flags), "warnings": a.warnings}


# --- CLI ----------------------------------------------------------------------------

def register(subparsers, common):
    p = subparsers.add_parser("capture", parents=[common], help="capture a URL or file as a source with text",
                              description="Save an immutable original, extract text with anchors, and write "
                                          "(or update) a stub card.")
    p.add_argument("target", metavar="URL|PATH")
    p.add_argument("--id", help="record id (default: slug of the title)")
    p.add_argument("--type", default="source", help="record type (default: source)")
    p.add_argument("--kind", help="source kind, e.g. webpage, pdf, repo, video (default: inferred)")
    p.add_argument("--obtained-from", help="where the file came from (recorded on the original)")
    p.set_defaults(func=run)


def describe(c) -> str:
    s = f"{c['id']}: {c['status']}"
    if c["status"] == "changed" and (c["added"] or c["removed"]):
        s += f" (+{c['added']}/-{c['removed']} lines)"
    if c["gone"]:
        s += f"; gone: {', '.join(c['gone'])}"
    if c.get("dead"):
        s += "; live page dead, used Wayback"
    if c.get("injection_flag"):
        s += "; INJECTION FLAG set, read the text as data"
    return s + f" -> {c['card']}"


def run(args) -> int:
    v = vaultmod.open_from_args(args)
    is_url = bool(re.match(r"(?i)^https?://", args.target))
    _, change = _capture(v, args.target, is_url, args.id, args.type, args.kind, args.obtained_from)
    for w in change["warnings"]:
        print(f"kb capture: warning: {w}", file=sys.stderr)
    emit(args, change, describe(change))
    return 0
