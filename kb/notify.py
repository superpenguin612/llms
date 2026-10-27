"""Alerts for watch events: ntfy, a webhook, or a command; immediately or as a digest (spec §2.11)."""

import json
import os
import shlex
import sqlite3
import subprocess
import urllib.request

from kb.util import iso, now_utc, parse_cadence, parse_time

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watch_state(target TEXT, record_id TEXT, state_json TEXT, checked_at TEXT,
                                       PRIMARY KEY(target, record_id));
CREATE TABLE IF NOT EXISTS watch_events(n INTEGER PRIMARY KEY, target TEXT, record_id TEXT, at TEXT,
                                        summary TEXT, detail_json TEXT, alerted INT);
"""
MAX_LINES = 30
MAX_LINE = 200
MAX_CHARS = 1900  # under Discord's 2000-character content limit


def connect(vault) -> sqlite3.Connection:
    """The vault index with the watch tables (kb build keeps watch_* and meta)."""
    conn = sqlite3.connect(vault.kb_dir / "kb.sqlite", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def message(vault_name: str, events) -> tuple[str, str]:
    """(title, text): vault name and count, then one `[record] summary` line per event."""
    title = f"kb {vault_name}: {len(events)} watch change{'s' * (len(events) != 1)}"
    lines = []
    for e in events[:MAX_LINES]:
        line = f"[{e['record_id']}] {e['summary']}"
        lines.append(line if len(line) <= MAX_LINE else line[:MAX_LINE - 1] + "…")
    if len(events) > MAX_LINES:
        lines.append(f"… and {len(events) - MAX_LINES} more (kb watch events)")
    text = title + "\n" + "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS - 40].rsplit("\n", 1)[0] + "\n… truncated (kb watch events)"
    return title, text


def _post(url, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, method="POST", headers={"User-Agent": "kb", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def _send(channel: str, alerts: dict, title: str, text: str) -> None:
    if channel == "ntfy":
        _post(alerts["ntfy"], text.encode("utf-8"),
              {"Title": title.encode("latin-1", "replace").decode("latin-1"), "Content-Type": "text/plain; charset=utf-8"})
    elif channel == "webhook":
        url = os.environ.get(alerts["webhook_env"])
        if not url:
            raise RuntimeError(f"env {alerts['webhook_env']} is not set; export the webhook URL there")
        _post(url, json.dumps({"text": text, "content": text}).encode("utf-8"), {"Content-Type": "application/json"})
    elif channel == "command":
        p = subprocess.run(shlex.split(str(alerts["command"])), input=text, text=True,
                           capture_output=True, timeout=60)
        if p.returncode:
            raise RuntimeError(f"exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")


def notify(vault, events=None, now=None) -> list:
    """Send unalerted watch events (or the given event rows) through every configured channel.
    Returns [{channel, ok, error}]; [] when nothing was sent. Never raises on a channel failure:
    events stay unalerted unless at least one channel succeeded."""
    alerts = vault.profile.get("alerts") or {}
    channels = [c for c, key in (("ntfy", "ntfy"), ("webhook", "webhook_env"), ("command", "command")) if alerts.get(key)]
    if not channels:
        return []
    now = now or now_utc()
    conn = connect(vault)
    try:
        digest = alerts.get("mode", "immediate") == "digest"
        if digest:
            row = conn.execute("SELECT value FROM meta WHERE key='alerts.last_digest'").fetchone()
            last = parse_time(row[0]) if row else None
            every = parse_cadence(alerts.get("digest_every", "6h"))
            if last and every and now - last < every:
                return []
        if events is None:
            events = [dict(r) for r in conn.execute("SELECT * FROM watch_events WHERE alerted=0 ORDER BY n")]
        if not events:
            return []
        title, text = message(vault.name, events)
        results = []
        for c in channels:
            try:
                _send(c, alerts, title, text)
                results.append({"channel": c, "ok": True, "error": ""})
            except Exception as e:  # a broken channel must not stop the run or the other channels
                results.append({"channel": c, "ok": False, "error": str(e) or type(e).__name__})
        if any(r["ok"] for r in results):
            conn.executemany("UPDATE watch_events SET alerted=1 WHERE n=?", [(e["n"],) for e in events if "n" in e])
            if digest:
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('alerts.last_digest', ?)", (iso(now),))
            conn.commit()
        return results
    finally:
        conn.close()
