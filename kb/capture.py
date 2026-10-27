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
import yaml

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
                          f"(kb capture FILE --id ...) or write the card by hand with an original_note saying why")
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
    """(host, owner, repo) of a repo URL (https, ssh or scp-style)."""
    m = _REPO.match(url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]*)[:/]+(.+?)(?:\.git)?/*$", url.strip())
    parts = [p for p in (m.group(2) if m else "").split("/") if p not in ("", ".", "..")]
    if len(parts) < 2:
        raise KBError(f"can't tell owner/repo from {url}; pass the clone URL")
    return (m.group(1) or "local").lower(), "/".join(parts[:-1]), parts[-1]


def _fetch_repo(vault, url, stage, ref=None):
    """Clone or pull, then read the README, file list and commit at HEAD, or at `ref` (branch, tag, pull/N/head
    or full sha; fetched from origin when the clone lacks it) without checking it out."""
    host, owner, name = _repo_parts(url)
    rel = f"{host}/{owner}/{name}"
    path = clones_root() / rel
    git = ["git", "-C", str(path)]
    restored = not (path / ".git").exists()
    if restored:
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", url, str(path)])
    elif not ref:
        _run(git + ["pull", "--ff-only", "--quiet"])
    if ref:
        have = re.fullmatch(r"[0-9a-f]{40}", ref) and \
            subprocess.run(git + ["cat-file", "-e", f"{ref}^{{commit}}"], capture_output=True).returncode == 0
        if not have:
            _run(git + ["fetch", "--quiet", "origin", ref])
        commit = _run(git + ["rev-parse", f"{ref if have else 'FETCH_HEAD'}^{{commit}}"]).strip()
        (stage / "tree.txt").write_text(_run(git + ["ls-tree", "-r", "--name-only", commit]), encoding="utf-8")
        (stage / "commit.txt").write_text(_run(git + ["log", "-1", commit]), encoding="utf-8")
        top = _run(git + ["ls-tree", "--name-only", commit]).splitlines()
        readme = next(iter(sorted(n for n in top if n.startswith("README"))), None)
        readme_text = _run(git + ["show", f"{commit}:{readme}"]) if readme else ""
        if readme:
            (stage / readme).write_text(readme_text, encoding="utf-8")
    else:
        commit = _run(git + ["rev-parse", "HEAD"]).strip()
        (stage / "tree.txt").write_text(_run(git + ["ls-files"]), encoding="utf-8")
        (stage / "commit.txt").write_text(_run(git + ["log", "-1"]), encoding="utf-8")
        readme = next(iter(sorted(p for p in path.glob("README*") if p.is_file())), None)
        readme_text = ""
        if readme:
            shutil.copy2(readme, stage / readme.name)
            readme_text = readme.read_text(encoding="utf-8", errors="replace")
    _run(git + ["update-ref", f"refs/kb/{commit}", commit])  # a pinned commit survives gc even if the remote drops it
    version = _run(["git", "--version"]).strip().replace("git version ", "git ")
    canonical = url if _REPO.match(url) is None else re.sub(r"(\.git)?/?$", "", url)
    title, rid = f"{owner}/{name}", None
    if ref:
        # its own card: a URL per commit keeps URL dedup from merging it into the HEAD card
        canonical = f"https://{host}/{owner}/{name}/tree/{commit}" if host in ("github.com", "gitlab.com", "codeberg.org") \
            else f"{canonical}@{commit}"
        label = commit[:10] if ref == commit else ref
        title, rid = f"{title} @ {label}", f"{short_id(title)}-{slugify(label)}"
    return SimpleNamespace(main="commit.txt", fetched_url=canonical if ref else url, http_status=None, via="git",
                           final_url=canonical, dead=False, text=readme_text, title=title, kind="repo",
                           restored=restored, clone_path=path, id=rid,
                           meta={"extracted_with": version, "clone": f"clone:{rel}@{commit}", "commit": commit})


# --- clones, capture manifests, dedup lookups ---------------------------------------------

def clones_root() -> Path:
    """Where repo clones and watch mirrors live: $KB_CLONES, else $XDG_DATA_HOME/kb/clones,
    else ~/.local/share/kb/clones."""
    if os.environ.get("KB_CLONES"):
        return vaultmod.expand(os.environ["KB_CLONES"])
    data = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return vaultmod.expand(data) / "kb" / "clones"


def parse_clone(text):
    """`clone:<ref>@<commit>` -> (path, commit, portable) or None. Portable refs are <host>/<owner>/<repo>
    under clones_root(); old refs are a machine path (absolute or ~)."""
    if not isinstance(text, str) or not text.startswith("clone:"):
        return None
    ref, _, commit = text[len("clone:"):].rpartition("@")
    if not ref:
        ref, commit = commit, ""
    portable = not ref.startswith(("/", "~"))
    return (clones_root() / ref if portable else vaultmod.expand(ref)), commit, portable


MANIFEST = "capture.yaml"
AUX_FILES = ("headers.txt", "tree.txt", "commit.txt", MANIFEST)


def file_entry(path: Path) -> dict:
    return {"name": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size, "format": _format(path.name)}


def write_manifest(folder: Path, info: dict, files=None) -> dict:
    """Write <folder>/capture.yaml: info keys (captured, via, given_url, fetched_url, http_status, obtained_from,
    main) plus `files` (default: every file in the folder, hashed now)."""
    if files is None:
        files = [file_entry(f) for f in sorted(folder.iterdir()) if f.is_file() and f.name != MANIFEST]
    keys = ("captured", "via", "given_url", "fetched_url", "http_status", "obtained_from", "main")
    m = {**_drop_none({k: info.get(k) for k in keys}), "files": files}
    atomic_write(folder / MANIFEST, yaml.safe_dump(m, sort_keys=False, allow_unicode=True, width=1000))
    return m


def read_manifest(folder: Path) -> dict:
    """Parsed capture.yaml; raises KBError when it is missing or malformed."""
    p = Path(folder) / MANIFEST
    try:
        m = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KBError(f"{p.name} missing") from None
    except yaml.YAMLError as e:
        raise KBError(f"{p.name} is not valid YAML: {str(e).splitlines()[0]}") from None
    if not isinstance(m, dict) or not isinstance(m.get("files"), list) or \
            not all(isinstance(f, dict) and f.get("name") for f in m["files"]):
        raise KBError(f"{p.name} needs a `files:` list of {{name, sha256, bytes, format}}")
    return m


def capture_dirs(vault):
    """(id, folder) for every originals/<id>/<date>/ folder."""
    base = vault.root / "originals"
    for d in sorted(base.iterdir()) if base.is_dir() else ():
        if d.is_dir():
            for c in sorted(d.iterdir(), key=lambda p: _natural(p.name)):
                if c.is_dir():
                    yield d.name, c


def _natural(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _index(vault) -> dict:
    """{"sha": {sha256: id}, "url": {norm url: id}} from flat front matter and every capture.yaml;
    cached on the vault for this run (capture adds to it)."""
    idx = vault.__dict__.get("_kb_capture_index")
    if idx is None:
        idx = {"sha": {}, "url": {}}
        for r in vault.records:
            if isinstance(r.fm.get("sha256"), str):
                idx["sha"].setdefault(r.fm["sha256"], r.id)
        for rid, d in capture_dirs(vault):
            try:
                _index_manifest(idx, rid, read_manifest(d))
            except KBError:
                continue
        vault.__dict__["_kb_capture_index"] = idx
    return idx


def _index_manifest(idx, rid, m):
    for f in m["files"]:
        if f.get("name") == m.get("main") and f.get("sha256"):
            idx["sha"].setdefault(f["sha256"], rid)
    for k in ("fetched_url", "given_url"):
        if m.get(k):
            idx["url"].setdefault(vaultmod.norm_url(str(m[k])), rid)


def find_by_sha(vault, sha: str):
    rid = _index(vault)["sha"].get(sha)
    return (vault.get(rid) if rid else None) or vault.find_by_sha(sha)


def find_by_url(vault, url: str):
    rid = _index(vault)["url"].get(vaultmod.norm_url(url))
    return vault.find_by_url(url) or (vault.get(rid) if rid else None)


def known_shas(vault, rid: str) -> set:
    """Main-file sha256s of every capture of one record (old nested list included)."""
    out = {sha for sha, i in _index(vault)["sha"].items() if i == rid}
    rec = vault.get(rid)
    if rec is not None:
        out |= {o.get("sha256") for o in rec.fm.get("originals") or [] if isinstance(o, dict)}
        if rec.fm.get("sha256"):
            out.add(rec.fm["sha256"])
    return out


def set_flat(fm: dict, original: str, sha256: str, captured: str) -> None:
    """Point the record at its latest original (Obsidian-flat keys), dropping the old nested list."""
    fm.pop("originals", None)
    fm.pop("originals_note", None)
    fm.pop("original_note", None)
    fm.update({"original": original, "sha256": sha256, "captured": captured})


def _ensure_commit(path: Path, commit: str, warnings: list) -> None:
    """Keep a pinned commit available in a clone (fetch it when a fresh clone lacks it) and pin it as
    refs/kb/<sha> so gc keeps it."""
    if not commit:
        return
    git = ["git", "-C", str(path)]
    have = lambda: subprocess.run(git + ["rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
                                  capture_output=True, text=True)
    p = have()
    if p.returncode and subprocess.run(git + ["fetch", "--quiet", "origin", commit], capture_output=True).returncode == 0:
        p = have()
    if p.returncode:
        warnings.append(f"pinned commit {commit[:12]} is no longer on the remote; repo excerpts at it can't be checked")
        return
    sha = p.stdout.strip()
    subprocess.run(git + ["update-ref", f"refs/kb/{sha}", sha], capture_output=True)


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

def short_id(title: str, max_len: int = 40) -> str:
    """Slug of the title, cut at a word boundary to at most max_len characters."""
    slug = slugify(title, 200)
    if len(slug) <= max_len:
        return slug
    head = slug[:max_len + 1]
    cut = head.rsplit("-", 1)[0] if "-" in head else ""
    return cut if len(cut) >= 12 else slug[:max_len].rstrip("-")


def _url_title(url: str) -> str:
    """Fallback title: the URL's last path segment without extension, else the host."""
    u = urllib.parse.urlparse(url)
    stem = Path(urllib.parse.unquote(u.path.rstrip("/"))).stem
    return stem if re.search(r"[A-Za-z]{3}", stem or "") else (u.hostname or url)


def _project_type(vault):
    """The profile's type for tracked projects: internal, watched, with 'project' among its kinds."""
    for td in vault.profile.types.values():
        kinds = td.keys.get("kind")
        if td.origin == "internal" and td.tracked == "watch" and isinstance(kinds, list) and "project" in kinds:
            return td
    return None


def link_project_entity(vault, card, url: str, watch: bool = False):
    """A captured repo (source card: what it said at one commit) is paired with a project entity
    (the living project: watch timeline, facts). Find it by handle or watch target, else create a stub;
    then make sure the card lists it in `entities`. The git watch target is added only when watch=True:
    many captured repos are one-off references. Returns the entity id, or None without a project type."""
    td = _project_type(vault)
    if td is None:
        return None
    host, owner, name = _repo_parts(url)
    path = f"{owner}/{name}"
    handle, target = f"{host.split('.')[0]}:{path}", f"git:{url.rstrip('/')}"
    ent = None
    for r in vault.of_type(td.name):
        if handle in (r.fm.get("handles") or []) or any(str(w).rstrip("/").removesuffix(".git") == target.removesuffix(".git")
                                                        for w in r.fm.get("watch") or []):
            ent = r
            break
    if ent is None:
        eid = vault.new_id(slugify(path.replace("/", "-")))
        fm = {"type": td.name, "title": path, "description": f"The {path} repository as a living project.",
              "kind": "project", "handles": [handle], "updated": dt.date.today()}
        body = (f"\n# {path}\n\n## Summary\nSnapshots of what it contained are source cards: [[{card.id}]].\n\n"
                f"## Facts\n\n## Timeline\n")
        ent = write_record(vault.root / td.folder_for(kind="project") / f"{eid}.md", fm, body)
        append_log(vault.root, "entity", eid, f"created for repo {url}")
    if watch and target not in (ent.fm.get("watch") or []):
        ent.fm["watch"] = [*(ent.fm.get("watch") or []), target]
        ent.save()
    link = f"[[{ent.path.stem}]]"
    ents = card.fm.get("entities") or []
    if link not in ents:
        card.fm["entities"] = [*ents, link]
        card.save()
    return ent.path.stem


def capture_url(vault, url, id=None, type="source", kind=None, obtained_from=None, set=None) -> Record:
    return _capture(vault, url, True, id, type, kind, obtained_from, set=set)[0]


def capture_file(vault, path, id=None, type="source", kind=None, obtained_from=None, set=None) -> Record:
    return _capture(vault, path, False, id, type, kind, obtained_from, set=set)[0]


def _capture(vault, src, is_url, id=None, type="source", kind=None, obtained_from=None, watch=False, ref=None, set=None):
    """Returns (record, change). change: {id, card, status: new|unchanged|same-text|changed, capture,
    added, removed, gone, dead, warnings}. ref: capture a repo at that branch/tag/commit as its own card.
    set: extra front-matter keys, applied before the folder is computed."""
    vault.reload()
    td = vault.profile.type(type)
    existing = vault.get(id) if id else None
    if existing is None and is_url and not ref:
        existing = find_by_url(vault, src)
    warn_b, max_b = _caps(vault)
    vault.kb_dir.joinpath("tmp").mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=vault.kb_dir / "tmp", prefix="capture-"))
    warnings = []
    try:
        if is_url:
            got = _fetch_repo(vault, src, stage, ref) if ref else \
                _route(src, kind or (existing.fm.get("kind") if existing else None))(vault, src, stage)
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
            existing = find_by_url(vault, got.final_url)
        main_sha = sha256_file(stage / got.main)
        if existing is None and not ref:
            existing = find_by_sha(vault, main_sha)
        restored = getattr(got, "restored", False) and existing is not None
        if restored:  # a fresh clone: fetch and pin the card's commit and every commit its excerpts quote
            old = parse_clone(existing.fm.get("text"))
            locs = [re.search(r"@\s*([0-9a-f]{4,40})\s*$", e.location) for e in parse_excerpts(existing.body)]
            for c in dict.fromkeys([old[1] if old else "", *(m.group(1) for m in locs if m)]):
                _ensure_commit(got.clone_path, c, warnings)
        if existing is not None:
            td = vault.typedef(existing) or td
            if main_sha in known_shas(vault, existing.id):
                return _touch(existing, td, warnings, "restored clone" if restored else "unchanged")
            if "originals" in existing.fm:
                raise KBError(f"{existing.rel} uses the old originals format; run `kb migrate originals`, then recapture")
        size = sum(f.stat().st_size for f in stage.iterdir())
        if size > warn_b:
            warnings.append(f"capture is {size / MB:.1f} MB, over caps.warn_mb")

        if got.text is None:
            text, meta = extract.extract(stage / got.main, kind if kind in extract.KINDS else None)
            ek = extract.detect(stage / got.main)
        else:
            text, meta, ek = got.text, got.meta, None
        title = (existing.title if existing else None) or got.title or meta.get("title") or \
            (_url_title(src) if is_url else Path(src).stem)
        if set and set.get("title"):
            title = set["title"]
        card_kind = kind or got.kind or CARD_KIND.get(Path(got.main).suffix.lower()) or CARD_KIND.get(ek)
        rid = existing.id if existing else (id or vault.new_id(getattr(got, "id", None) or short_id(title)))

        final_dir = _date_dir(vault.root / "originals" / rid)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final_dir)
        captured = iso(now_utc())
        manifest = write_manifest(final_dir, {
            "captured": captured, "via": got.via, "given_url": src if is_url and src != got.fetched_url and not ref else None,
            "fetched_url": got.fetched_url, "http_status": got.http_status, "obtained_from": obtained_from,
            "main": got.main})
        _index_manifest(_index(vault), rid, manifest)
        originals = {"original": vault.rel(final_dir / got.main), "sha256": main_sha, "captured": captured}
        flags = inject.scan(text)
        args = SimpleNamespace(vault=vault, rid=rid, td=td, got=got, text=text, meta=meta, title=title,
                               kind=card_kind, src=(got.final_url if ref else src) if is_url else None,
                               originals=originals, flags=flags,
                               capture=vault.rel(final_dir), warnings=warnings, set=set or {})
        rec, change = _update(args, existing) if existing else _create(args)
        append_log(vault.root, "capture", rid, f"{change['status']} {src if is_url else Path(src).name}" + (f" @ {ref}" if ref else ""))
        vault.reload()
        if card_kind == "repo" and is_url:
            ent = link_project_entity(vault, rec, src, watch=watch)
            if ent:
                change["entity"] = ent
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
    set_flat(fm, **a.originals)
    _text_fields(a, fm)
    if a.td.tracked == "refresh":
        fm["last_checked"] = _day()
    if a.kind == "image":
        fm["file"] = f"[[{Path(a.got.main).name}]]"
    fm = _drop_none(fm)
    fm.update(a.set)
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
    set_flat(fm, **a.originals)
    fm.update(a.set)
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
    if a.set.get("title"):
        body = re.sub(r"(?m)^#[ \t]+.+$", lambda _: f"# {a.set['title']}", body, count=1)
    rec.body = body
    rec.save()
    return rec, change


def _date_dir_named(base: Path, stamp: str) -> Path:
    p, n = base / f"{stamp}.md", 2
    while p.exists():
        p, n = base / f"{stamp}-{n}.md", n + 1
    return p


def _touch(rec, td, warnings, status="unchanged"):
    """Same bytes as an existing original: only bump last_checked."""
    if td.tracked == "refresh" or "last_checked" in rec.fm:
        rec.fm["last_checked"] = _day()
        rec.save()
    return rec, {"id": rec.id, "card": rec.rel, "status": status, "capture": None, "added": 0, "removed": 0,
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
    p.add_argument("--watch", action="store_true", help="repos: also watch the repo for new commits (kb watch)")
    p.add_argument("--ref", help="repos: capture this branch, tag, pull/N/head or full commit sha as its own card "
                                 "(fetched from origin if the clone lacks it)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="extra front-matter key (repeatable); the value is read as YAML, e.g. --set event=ctf-1")
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
    keys = {}
    for kv in args.set:
        k, eq, val = kv.partition("=")
        if not eq or not k.strip():
            raise KBError(f"--set takes KEY=VALUE, got {kv!r}")
        keys[k.strip()] = yaml.safe_load(val)
    if keys:
        from kb import triage  # late: triage imports capture
        td = v.profile.type(args.type)
        errs = triage._check_set(v, "--set", "capture", td, keys)
        if errs:
            raise KBError("\n".join(errs))
    is_url = bool(re.match(r"(?i)^https?://", args.target)) or bool(args.ref)
    _, change = _capture(v, args.target, is_url, args.id, args.type, args.kind, args.obtained_from, watch=args.watch,
                         ref=args.ref, set=keys)
    for w in change["warnings"]:
        print(f"kb capture: warning: {w}", file=sys.stderr)
    emit(args, change, describe(change))
    return 0
