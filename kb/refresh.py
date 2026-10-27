"""kb refresh [--due | ID...]: recapture tracked: refresh records and report what changed (spec §2.8, §2.6 Gone).

The capture itself (new dated original, text history, ## Changes, Gone: lines) is capture._capture;
this module picks the records, handles failures (status: stale), and prints the change list.
"""

import sys

from kb import capture, vault as vaultmod
from kb.util import KBError, append_log, emit, is_due, today


def due_records(v, ids=(), due=False):
    if ids:
        recs = [v.require(i) for i in ids]
        bad = [r.id for r in recs if (v.typedef(r) is None or v.typedef(r).tracked != "refresh")]
        if bad:
            raise KBError(f"not a tracked: refresh record: {', '.join(bad)}; only types with tracked: refresh refresh")
        return recs
    if not due:
        raise KBError("say what to refresh: kb refresh --due, or kb refresh ID...")
    events = v.profile.event_dates
    return [r for r in v.records if (t := v.typedef(r)) and t.tracked == "refresh"
            and is_due(r.fm.get("last_checked"), r.fm.get("refresh") or t.refresh, event_dates=events)]


def refresh_one(v, rec) -> dict:
    url = rec.fm.get("url") or rec.fm.get("given_url")
    if not url:
        return {"id": rec.id, "status": "skipped", "reason": "no url; recapture files with kb capture PATH --id " + rec.id}
    try:
        _, change = capture._capture(v, str(url), True, id=rec.id, type=rec.type, kind=rec.fm.get("kind"))
    except KBError as e:
        rec = v.require(rec.id)  # capture reloads the vault
        rec.fm["status"] = "stale"
        rec.fm["last_checked"] = capture._day()
        rec.body = capture.add_change(rec.body, f"- {today()}: recapture failed: {e}")
        rec.save()
        return {"id": rec.id, "status": "failed", "error": str(e)}
    return change


def run(args) -> int:
    v = vaultmod.open_from_args(args)
    results = [refresh_one(v, r) for r in due_records(v, args.ids, args.due)]
    changed = [r for r in results if r["status"] in ("changed", "failed")]
    append_log(v.root, "refresh", "--due" if args.due else ",".join(args.ids),
               f"{len(results)} checked, {len(changed)} changed or failed")
    for r in results:
        for w in r.get("warnings") or []:
            print(f"kb refresh: warning: {r['id']}: {w}", file=sys.stderr)

    def text(rs):
        if not rs:
            return "nothing due"
        return [capture.describe(r) if "card" in r else f"{r['id']}: {r['status']}: {r.get('error') or r.get('reason')}"
                for r in rs]
    emit(args, results, text)
    return 0


def register(subparsers, common):
    p = subparsers.add_parser("refresh", parents=[common], help="recapture tracked sources and report changes",
                              description="Recapture tracked: refresh records into new dated originals; mark "
                                          "excerpts no longer present as Gone; append ## Changes.")
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--due", action="store_true", help="every record past its refresh cadence")
    p.set_defaults(func=run)
