import contextlib
import io
import json
import os
import subprocess
import urllib.error
from pathlib import Path
from unittest import mock

import yaml

from kb import cli, watch
from kb.notify import connect
from kb.records import read_record
from kb.vault import open_vault
from tests.helpers import VaultTestCase, make_vault

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def entity(rid, watch_list, **fm):
    return (f"entities/team/{rid}.md", ({"type": "entity", "title": rid, "kind": "team", "watch": watch_list, **fm},
                                        f"\n# {rid}\n\n## Notes\nx\n"))


class FakeGit:
    """Stands in for subprocess.run: ls-remote output per call, plus mirror commands."""

    def __init__(self, *ls_outputs, ancestor=0, log=""):
        self.ls = list(ls_outputs)
        self.ancestor, self.log, self.calls = ancestor, log, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        sub = [c for c in cmd[1:] if not c.startswith("-") and "/" not in c]
        if "ls-remote" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.ls.pop(0), "")
        if "merge-base" in cmd:
            return subprocess.CompletedProcess(cmd, self.ancestor, "", "")
        if "log" in sub:
            return subprocess.CompletedProcess(cmd, 0, self.log, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


A, B, C = "a" * 40, "b" * 40, "c" * 40


class TestWatch(VaultTestCase):
    def vault(self, recs, **overrides):
        root = make_vault(self.tmp / "v", records=dict(recs), overrides=overrides)
        return open_vault(start=root)

    def test_git_baseline_then_changes(self):
        v = self.vault([entity("rival", ["git:https://github.com/rival/tool"])])
        fake = FakeGit(f"{A}\trefs/heads/main\n{A}\trefs/tags/v1\n{C}\trefs/tags/v1^{{}}\n",
                       f"{B}\trefs/heads/main\n{A}\trefs/heads/dev\n", log="one\n")
        with mock.patch("kb.watch.subprocess.run", fake):
            r1 = watch.run(v, alert=False)
            v.reload()
            r2 = watch.run(v, all_=True, alert=False)
        self.assertEqual(r1["records"][0]["baseline"], ["git:https://github.com/rival/tool"])
        self.assertEqual(r1["records"][0]["changes"], [])
        sums = [c["summary"] for c in r2["records"][0]["changes"]]
        self.assertEqual(sums, ["new branch dev (aaaaaaa)", "main: aaaaaaa → bbbbbbb (1 commit: one)", "tag v1 deleted (was ccccccc)"])
        rec = read_record(v.root / "entities/team/rival.md")
        self.assertIn("last_checked", rec.fm)
        self.assertRegex(rec.body, r"## Timeline\n- \d{4}-\d\d-\d\d \d\d:\d\dZ — new branch dev")
        conn = connect(v)
        self.assertEqual(conn.execute("SELECT count(*) FROM watch_events WHERE alerted=0").fetchone()[0], 3)
        self.assertIn("watch | rival | main:", (v.root / "log.md").read_text())

    def test_not_due_is_skipped(self):
        v = self.vault([entity("rival", ["git:https://x/y"], last_checked="2999-01-01T00:00:00Z")])
        with mock.patch("kb.watch.subprocess.run", FakeGit()):
            self.assertEqual(watch.run(v, alert=False)["records"], [])

    def test_mirror_commits_and_force_push(self):
        os.environ["KB_CLONES"] = str(self.tmp / "mirrors")
        v = self.vault([entity("rival", ["git:git@github.com:rival/tool.git"])])
        fake = FakeGit(f"{A}\trefs/heads/main\n", f"{B}\trefs/heads/main\n", ancestor=0, log="fix one\nadd two\n")
        with mock.patch("kb.watch.subprocess.run", fake):
            watch.run(v, alert=False)
            ch = watch.run(v, all_=True, alert=False)["records"][0]["changes"][0]
        self.assertEqual(ch["summary"], "main: aaaaaaa → bbbbbbb (2 commits: fix one; add two)")
        clone = [c for c in fake.calls if "clone" in c][0]
        self.assertTrue(clone[-1].endswith("mirrors/github.com/rival/tool.git"))
        fake2 = FakeGit(f"{C}\trefs/heads/main\n", ancestor=1)
        (self.tmp / "mirrors/github.com/rival/tool.git").mkdir(parents=True, exist_ok=True)
        (self.tmp / "mirrors/github.com/rival/tool.git/HEAD").write_text("ref: refs/heads/main\n")
        with mock.patch("kb.watch.subprocess.run", fake2):
            ch = watch.run(v, all_=True, alert=False)["records"][0]["changes"][0]
        self.assertTrue(ch["detail"]["forced"])
        self.assertIn("force-push", ch["summary"])
        self.assertTrue(any("fetch" in c for c in fake2.calls))

    def test_github_user_rate_limit_keeps_state(self):
        v = self.vault([entity("rival", ["github-user:rival"]), entity("other", ["feed:https://e.com/rss"])])
        repos1 = json.dumps([{"full_name": "rival/a", "pushed_at": "1"}]).encode()
        repos2 = json.dumps([{"full_name": "rival/a", "pushed_at": "2"}, {"full_name": "rival/b", "pushed_at": "3"}]).encode()
        rss = b"<rss><channel><item><guid>1</guid><title>One</title></item></channel></rss>"
        err = urllib.error.HTTPError("u", 403, "rate", {"X-RateLimit-Reset": "123"}, None)
        seq = iter([rss, repos1, rss, err, repos2])  # records run in path order: other, then rival

        def urlopen(req, timeout=0):
            x = next(seq)
            if isinstance(x, Exception):
                raise x
            return Resp(x)
        with mock.patch("kb.watch.urllib.request.urlopen", urlopen):
            watch.run(v, alert=False)
            first = read_record(v.root / "entities/team/rival.md").fm["last_checked"]
            v.reload()
            r = watch.run(v, all_=True, alert=False, now=watch.now_utc().replace(year=2099))
            self.assertIn("rate limited", r["records"][1]["errors"][0]["error"])
            self.assertEqual(r["records"][0]["errors"], [])
            self.assertEqual(read_record(v.root / "entities/team/rival.md").fm["last_checked"], first)
            v.reload()
            r = watch.run(v, ["rival"], alert=False)
        self.assertEqual([c["summary"] for c in r["records"][0]["changes"]], ["rival/a pushed (2)", "new repo rival/b"])

    def test_feed_and_page(self):
        atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>x1</id><title>Old</title></entry></feed>',
                b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>x2</id><title>New</title></entry>'
                b'<entry><id>x1</id><title>Old</title></entry></feed>')
        with mock.patch("kb.watch._http", side_effect=atom):
            s1, c1 = watch.check_feed(None, "u", None)
            s2, c2 = watch.check_feed(None, "u", s1)
        self.assertEqual(c1, [])
        self.assertEqual([c[0] for c in c2], ["new item: New"])
        pages = [b"<p>Hello <b>world</b></p><script>x=1</script>", b"<p>Hello   world</p>", b"<p>Bye</p>"]
        with mock.patch("kb.watch._http", side_effect=pages):
            s1, _ = watch.check_page(None, "u", None)
            s2, c2 = watch.check_page(None, "u", s1)
            s3, c3 = watch.check_page(None, "u", s2)
        self.assertEqual(c2, [])
        self.assertEqual([c[0] for c in c3], ["page changed"])

    def test_lock_held(self):
        import fcntl
        v = self.vault([entity("rival", ["git:https://x/y"])])
        with open(v.kb_dir / "watch.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertIsNone(watch.run(v, alert=False))

    def test_cli_add_list_events(self):
        root = make_vault(self.tmp / "v", records=dict([entity("rival", [])]))
        out = io.StringIO()
        with contextlib.chdir(root), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["watch", "add", "rival", "github-releases:rival/tool"]), 0)
            self.assertEqual(cli.main(["watch", "list"]), 0)
            self.assertEqual(cli.main(["watch", "events"]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["watch", "add", "rival", "bogus:x"]), 2)
        self.assertIn("cron: */15 * * * * cd ", out.getvalue())
        self.assertIn("github-releases:rival/tool", out.getvalue())
        self.assertEqual(read_record(root / "entities/team/rival.md").fm["watch"], ["github-releases:rival/tool"])

    def test_mirror_path(self):
        os.environ["KB_CLONES"] = "/m"
        self.assertEqual(watch.mirror_path("https://github.com/o/r.git").as_posix(), "/m/github.com/o/r.git")
        self.assertEqual(watch.mirror_path("git@gitlab.com:g/s/r").as_posix(), "/m/gitlab.com/g/s/r.git")
        self.assertEqual(watch.mirror_path("file:///srv/../r").as_posix(), "/m/local/srv/r.git")
        os.environ.pop("KB_CLONES")
        os.environ["XDG_DATA_HOME"] = "/xdg"
        self.addCleanup(os.environ.pop, "XDG_DATA_HOME", None)
        self.assertEqual(watch.mirror_path("https://github.com/o/r").as_posix(), "/xdg/kb/clones/github.com/o/r.git")


class TestWatchSnapshot(VaultTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, GIT_ENV)
        p.start()
        self.addCleanup(p.stop)

    def test_snapshot_real_git(self):
        src = self.tmp / "src"
        src.mkdir()
        for cmd in (["init", "-q", "-b", "main"], ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", str(src), *cmd], check=True)
        (src / "a.txt").write_text("hi")
        subprocess.run(["git", "-C", str(src), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "a"], check=True)
        url = src.as_uri()
        root = make_vault(self.tmp / "v", records=dict([entity("rival", [f"git:{url}"])]))
        v = open_vault(start=root)
        res = watch.snapshot(v, "rival")
        self.assertTrue(res["new"])
        self.assertTrue((self.tmp / "clones/local").is_dir())  # mirror under KB_CLONES
        rec = read_record(root / "entities/team/rival.md")
        self.assertNotIn("original", rec.fm)  # entity isn't original: immutable: folder + manifest only
        self.assertRegex(res["path"], r"^originals/rival/\d{4}-\d\d-\d\d/repo-[0-9a-f]{7}\.tar\.gz$")
        m = yaml.safe_load((root / res["path"]).parent.joinpath("capture.yaml").read_text())
        self.assertEqual((m["obtained_from"], m["via"], m["main"]), (f"{url}@{res['sha']}", "git archive", Path(res["path"]).name))
        self.assertEqual(m["files"][0]["bytes"], (root / res["path"]).stat().st_size)
        v.reload()
        self.assertFalse(watch.snapshot(v, "rival")["new"])
        from kb.lint import Linter
        bad = [f for f in Linter(open_vault(start=root)).run() if f.rule in ("manifest", "orphan-original", "fixity")]
        self.assertEqual(bad, [])
