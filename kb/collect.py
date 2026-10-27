"""kb collect ID [--collector github|crtsh|site]: run collectors for an entity's handles.

Results are cached as dated JSON in .kb/collect/<id>/<collector>/<date>.json; the command prints items
that are new since the previous cache. It never edits the entity note (the site collector captures pages
as source records that link to the entity).
"""

import json
import os
import re
import urllib.parse

from kb import capture, vault as vaultmod
from kb.util import KBError, append_log, atomic_write, emit, iso, now_utc, today

GITHUB = "https://api.github.com"
COLLECTORS = {"github": "github", "crtsh": "domain", "site": "site"}  # collector -> handle prefix


def _get_json(url, headers=None):
    try:
        r = capture.http_get(url, headers=headers, timeout=60)
    except OSError as e:
        raise KBError(f"network error fetching {url}: {getattr(e, 'reason', e)}") from None
    if r.status == 403 and "api.github.com" in url:
        raise KBError("GitHub API refused (403, likely the rate limit); set a token in GITHUB_TOKEN "
                      "or the env named by collectors.github.token_env")
    if r.status >= 400:
        raise KBError(f"{url} returned HTTP {r.status}")
    try:
        return json.loads(r.body or b"null"), r
    except ValueError:
        raise KBError(f"{url} did not return JSON") from None


def _next_link(resp):
    link = dict((k.lower(), v) for k, v in resp.headers).get("link", "")
    m = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return m.group(1) if m else None


def _gh_pages(url, headers, max_pages=10):
    out = []
    for _ in range(max_pages):  # shortcut: stops at 1000 repos/releases; raise max_pages for bigger orgs
        data, r = _get_json(url, headers)
        out.extend(data or [])
        url = _next_link(r)
        if not url:
            break
    return out


def collect_github(v, handle):
    env = ((v.profile.get("collectors") or {}).get("github") or {}).get("token_env") or "GITHUB_TOKEN"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get(env):
        headers["Authorization"] = f"Bearer {os.environ[env]}"
    items = []
    if "/" in handle:  # a project repo: its metadata and releases
        repos = [_get_json(f"{GITHUB}/repos/{handle}", headers)[0]]
        for rel in _gh_pages(f"{GITHUB}/repos/{handle}/releases?per_page=100", headers):
            items.append({"key": f"release:{handle}@{rel.get('tag_name')}", "kind": "release", "repo": handle,
                          "tag": rel.get("tag_name"), "name": rel.get("name"), "date": rel.get("published_at"),
                          "url": rel.get("html_url"), "prerelease": rel.get("prerelease")})
    else:
        user = _get_json(f"{GITHUB}/users/{handle}", headers)[0] or {}
        base = "orgs" if user.get("type") == "Organization" else "users"
        repos = _gh_pages(f"{GITHUB}/{base}/{handle}/repos?per_page=100&sort=pushed", headers)
        items.append({"key": f"account:{handle}", "kind": "account", "login": handle, "type": user.get("type"),
                      "name": user.get("name"), "date": user.get("created_at"), "url": user.get("html_url"),
                      "public_repos": user.get("public_repos"), "followers": user.get("followers")})
    for r in repos:
        items.append({"key": f"repo:{r.get('full_name')}", "kind": "repo", "repo": r.get("full_name"),
                      "url": r.get("html_url"), "description": r.get("description"), "date": r.get("created_at"),
                      "pushed_at": r.get("pushed_at"), "stars": r.get("stargazers_count"),
                      "forks": r.get("forks_count"), "language": r.get("language"), "topics": r.get("topics") or [],
                      "fork": r.get("fork"), "archived": r.get("archived")})
    return items


def collect_crtsh(v, domain):
    data, _ = _get_json(f"https://crt.sh/?q={urllib.parse.quote('%.' + domain)}&output=json")
    first = {}
    for row in data or []:
        seen = row.get("not_before") or row.get("entry_timestamp") or ""
        for name in str(row.get("name_value", "")).lower().split("\n"):
            name = name.strip()
            if name and (name not in first or seen < first[name]):
                first[name] = seen
    return [{"key": f"name:{n}", "kind": "name", "name": n, "date": d,
             "url": f"https://crt.sh/?q={urllib.parse.quote(n)}"} for n, d in sorted(first.items())]


def collect_site(v, url, entity):
    rec, change = capture._capture(v, url, True, obtained_from=f"kb collect {entity.id}")
    link = f"[[{entity.id}]]"
    if link not in (rec.fm.get("entities") or []):
        rec.fm.setdefault("entities", []).append(link)
        rec.save()
    return [{"key": f"capture:{rec.id}:{change['capture'] or 'unchanged'}", "kind": "capture", "url": url,
             "source": f"[[{rec.id}]]", "status": change["status"], "date": today()}]


def handles(entity) -> dict:
    """{prefix: [values]} from `handles: [github:x, domain:y, site:url]`."""
    out = {}
    for h in entity.fm.get("handles") or []:
        prefix, sep, value = str(h).partition(":")
        if sep and value.strip():
            out.setdefault(prefix.strip().lower(), []).append(value.strip())
    return out


def _cache_dir(v, rid, collector):
    return v.kb_dir / "collect" / rid / collector


def previous_keys(v, rid, collector) -> set:
    d = _cache_dir(v, rid, collector)
    files = sorted(p for p in d.glob("*.json") if p.stem != today()) if d.is_dir() else []
    if not files:
        return set()
    return {i.get("key") for i in json.loads(files[-1].read_text(encoding="utf-8")).get("items", [])}


def run_collector(v, entity, name, values) -> dict:
    items, errors = [], []
    for value in values:
        try:
            items += collect_site(v, value, entity) if name == "site" else \
                {"github": collect_github, "crtsh": collect_crtsh}[name](v, value)
        except KBError as e:
            errors.append(f"{value}: {e}")
    prev = previous_keys(v, entity.id, name)
    new = [i for i in items if i["key"] not in prev]
    atomic_write(_cache_dir(v, entity.id, name) / f"{today()}.json",
                 json.dumps({"entity": entity.id, "collector": name, "collected_at": iso(now_utc()),
                             "handles": values, "errors": errors, "items": items}, indent=1, ensure_ascii=False))
    return {"collector": name, "items": len(items), "new": new, "first_run": not prev, "errors": errors}


def run(args) -> int:
    v = vaultmod.open_from_args(args)
    entity = v.require(args.id)
    hs = handles(entity)
    names = [args.collector] if args.collector else [c for c, prefix in COLLECTORS.items() if prefix in hs]
    if not names:
        raise KBError(f"{entity.id} has no collectable handles; add e.g. handles: [github:LOGIN, domain:x.com, "
                      f"site:https://...] to its front matter")
    results = []
    for name in names:
        values = hs.get(COLLECTORS[name])
        if not values:
            raise KBError(f"{entity.id} has no {COLLECTORS[name]}: handle for the {name} collector")
        results.append(run_collector(v, entity, name, values))
    append_log(v.root, "osint", entity.id,
               "; ".join(f"{r['collector']}: {r['items']} items, {len(r['new'])} new" for r in results))

    def text(rs):
        lines = []
        for r in rs:
            lines.append(f"{r['collector']}: {r['items']} items, {len(r['new'])} new"
                         + (" (first run)" if r["first_run"] else ""))
            lines += [f"  {i['key']}" + (f" · {i['date']}" if i.get("date") else "") +
                      (f" · {i['url']}" if i.get("url") else "") for i in r["new"]]
            lines += [f"  error: {e}" for e in r["errors"]]
        return lines
    emit(args, results, text)
    return 1 if any(r["errors"] and not r["items"] for r in results) else 0


def register(subparsers, common):
    p = subparsers.add_parser("collect", parents=[common], help="run OSINT collectors for an entity's handles",
                              description="Cache dated collector results under .kb/collect/ and print new items. "
                                          "Never edits the entity note.")
    p.add_argument("id", metavar="ID")
    p.add_argument("--collector", choices=sorted(COLLECTORS))
    p.set_defaults(func=run)
