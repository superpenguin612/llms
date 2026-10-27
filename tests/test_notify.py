import datetime as dt
import json
import os
import subprocess
from unittest import mock

from kb import notify
from kb.vault import open_vault
from tests.helpers import VaultTestCase, make_vault
from tests.test_watch import Resp

T0 = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)


class TestNotify(VaultTestCase):
    def vault(self, alerts):
        v = open_vault(start=make_vault(self.tmp / "v", overrides={"name": "demo", "alerts": alerts}))
        conn = notify.connect(v)
        conn.executemany("INSERT INTO watch_events(target, record_id, at, summary, detail_json, alerted) "
                         "VALUES('git:x', ?, '2026-10-27T00:00:00Z', ?, '{}', 0)", [("rival", "main moved"), ("b", "new tag v2")])
        conn.commit()
        conn.close()
        return v

    def unalerted(self, v):
        return notify.connect(v).execute("SELECT count(*) FROM watch_events WHERE alerted=0").fetchone()[0]

    def test_immediate_all_channels(self):
        v = self.vault({"ntfy": "https://ntfy.example/t", "webhook_env": "KB_TEST_HOOK", "command": "cat -"})
        posts = []

        def urlopen(req, timeout=0):
            posts.append(req)
            return Resp(b"")
        with mock.patch.dict(os.environ, {"KB_TEST_HOOK": "https://hook.example/x"}), \
                mock.patch("kb.notify.urllib.request.urlopen", urlopen), \
                mock.patch("kb.notify.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            res = notify.notify(v)
        self.assertTrue(all(r["ok"] for r in res), res)
        self.assertEqual(posts[0].get_header("Title"), "kb demo: 2 watch changes")
        self.assertIn("[rival] main moved", posts[0].data.decode())
        body = json.loads(posts[1].data)
        self.assertEqual(body["text"], body["content"])
        self.assertEqual(run.call_args.kwargs["input"].splitlines()[1:], ["[rival] main moved", "[b] new tag v2"])
        self.assertEqual(self.unalerted(v), 0)
        self.assertEqual(notify.notify(v), [])

    def test_failure_keeps_unalerted(self):
        v = self.vault({"ntfy": "https://ntfy.example/t", "webhook_env": "KB_UNSET_HOOK"})
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("kb.notify.urllib.request.urlopen", side_effect=OSError("down")):
            os.environ.pop("KB_UNSET_HOOK", None)
            res = notify.notify(v)
        self.assertEqual([r["ok"] for r in res], [False, False])
        self.assertIn("down", res[0]["error"])
        self.assertEqual(self.unalerted(v), 2)

    def test_digest(self):
        v = self.vault({"command": "true", "mode": "digest", "digest_every": "6h"})
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("kb.notify.subprocess.run", return_value=ok) as run:
            self.assertEqual(len(notify.notify(v, now=T0)), 1)
            conn = notify.connect(v)
            conn.execute("INSERT INTO watch_events(target, record_id, at, summary, detail_json, alerted) "
                         "VALUES('g', 'c', 'x', 'later', '{}', 0)")
            conn.commit()
            self.assertEqual(notify.notify(v, now=T0 + dt.timedelta(hours=1)), [])
            self.assertEqual(len(notify.notify(v, now=T0 + dt.timedelta(hours=7))), 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.unalerted(v), 0)

    def test_no_channels_and_truncation(self):
        v = self.vault({})
        self.assertEqual(notify.notify(v), [])
        title, text = notify.message("demo", [{"record_id": "r", "summary": "x" * 500}] * 100)
        self.assertLessEqual(len(text), notify.MAX_CHARS)
        self.assertTrue(text.startswith("kb demo: 100 watch changes"))
        self.assertIn("…", text)
