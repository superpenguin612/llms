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
        self.assertEqual(sums, ["new branch dev (aaaaaaa)", "main: aaaaaaa → bbbbbbb (1 commit: one)", "tag v1 deleted (was ccccccc; old head pinned)"])
        rec = read_record(v.root / "entities/team/rival.md")
        self.assertIn("last_checked", rec.fm)
        self.assertRegex(rec.body, r"## Timeline\n- \d{4}-\d\d-\d\d \d\d:\d\dZ — new branch dev")
        conn = connect(v)
        self.assertEqual(conn.execute("SELECT count(*) FROM watch_events WHERE alerted=0").fetchone()[0], 3)
        self.assertIn("watch | rival | main:", (v.root / "log.md").read_text())

    def test_not_due_is_skipped(self):
        v = self.vault([entity("rival", ["git:https://example.com/o/r"])])
        with mock.patch("kb.watch.subprocess.run", FakeGit(f"{A}\trefs/heads/main\n", f"{A}\trefs/heads/main\n")):
            self.assertEqual(len(watch.run(v, alert=False)["records"]), 1)  # never checked: due
            self.assertEqual(watch.run(v, alert=False)["records"], [])      # just checked: not due

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
            checked = lambda: connect(v).execute(
                "SELECT checked_at FROM watch_state WHERE record_id='rival' AND target LIKE 'github-user:%'").fetchone()[0]
            first = checked()
            v.reload()
            r = watch.run(v, all_=True, alert=False, now=watch.now_utc().replace(year=2099))
            self.assertIn("rate limited", r["records"][1]["errors"][0]["error"])
            self.assertEqual(r["records"][0]["errors"], [])
            self.assertEqual(checked(), first)  # a failed target keeps its old check time, so it stays due
            self.assertNotIn("last_checked", read_record(v.root / "entities/team/rival.md").fm)  # quiet: file untouched
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
        self.assertIn("kb watch run --due --push", out.getvalue())
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


class TestWatchRealGit(VaultTestCase):
    """A local repo as the remote: rewritten history stays in the mirror."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, GIT_ENV)
        p.start()
        self.addCleanup(p.stop)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.git("init", "-q", "-b", "main")
        self.v = open_vault(start=make_vault(self.tmp / "v", records=dict([entity("rival", [f"git:{self.src.as_uri()}"])])))

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.src), *args], check=True, capture_output=True, text=True).stdout

    def commit(self, files, msg, rm=()):
        for name, text in files.items():
            (self.src / name).parent.mkdir(parents=True, exist_ok=True)
            (self.src / name).write_text(text)
        for name in rm:
            (self.src / name).unlink()
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def test_force_push_and_deleted_branch_are_pinned(self):
        base = self.commit({"a.txt": "a"}, "base")
        old = self.commit({"b.txt": "b"}, "to be rewritten")
        self.git("branch", "dev")
        watch.run(self.v, alert=False)  # baseline + mirror
        mirror = watch.mirror_path(self.src.as_uri())
        self.git("update-ref", "refs/recovered/x", base, cwd=mirror)  # a foreign ref in the mirror
        self.git("reset", "-q", "--hard", base)
        self.commit({"c.txt": "c"}, "rewritten")
        self.git("branch", "-D", "dev")
        self.v.reload()
        sums = [c["summary"] for c in watch.run(self.v, all_=True, alert=False)["records"][0]["changes"]]
        self.assertIn(f"dev deleted (was {old[:7]}; old head pinned)", sums)
        self.assertTrue(any("force-push; old head pinned" in s for s in sums), sums)
        self.v.reload()
        watch.run(self.v, all_=True, alert=False)  # a later --prune fetch keeps the pin
        self.git("gc", "-q", "--prune=now", cwd=mirror)
        refs = self.git("for-each-ref", "--format=%(refname)", cwd=mirror).split()
        self.assertTrue({f"refs/kb/{old}", "refs/recovered/x"} <= set(refs), refs)
        self.assertNotIn("refs/heads/dev", refs)  # --prune still follows the remote


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


class TestCommitPush(VaultTestCase):
    def test_commit_push_and_rebase_when_remote_moved(self):
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        os.environ.update(GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="user.name", GIT_CONFIG_VALUE_0="t",
                          GIT_CONFIG_KEY_1="user.email", GIT_CONFIG_VALUE_1="t@example.com",
                          GIT_CONFIG_KEY_2="commit.gpgsign", GIT_CONFIG_VALUE_2="false")
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in list(os.environ) if k.startswith("GIT_CONFIG_")])
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        root = make_vault(self.tmp / "vault", records={"entities/team/r.md": ({"type": "entity", "title": "R", "kind": "team"}, "\n# R\n")},
                          files={"notes.txt": "x"})
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "init"],
                    ["remote", "add", "origin", str(remote)], ["push", "-q", "-u", "origin", "main"]):
            subprocess.run(g + ["-C", str(root)] + cmd, check=True)
        other = self.tmp / "other"
        subprocess.run(g + ["clone", "-q", str(remote), str(other)], check=True)
        (other / "teammate.md").write_text("hi\n")
        subprocess.run(g + ["-C", str(other), "add", "-A"], check=True)
        subprocess.run(g + ["-C", str(other), "commit", "-qm", "teammate"], check=True)
        subprocess.run(g + ["-C", str(other), "push", "-q"], check=True)   # remote moved

        v = open_vault(start=root)
        rec = v.get("r")
        (rec.path).write_text(rec.path.read_text() + "\n## Timeline\n- change\n")
        (root / "notes.txt").write_text("unrelated local edit")               # must not be committed
        res = watch.commit_push(v, [{"id": "r", "path": str(rec.path), "changes": [{}]}], push=True)
        self.assertEqual((res["committed"], res["pushed"], res.get("error")), (True, True, None))
        log = subprocess.run(["git", "-C", str(remote), "log", "--format=%s", "main"], capture_output=True, text=True).stdout
        self.assertEqual(log.split("\n")[:2], ["kb watch: 1 change(s) in r", "teammate"])
        self.assertEqual((root / "notes.txt").read_text(), "unrelated local edit")  # autostash restored it
        shown = subprocess.run(["git", "-C", str(remote), "show", "--stat", "--format=", "main"], capture_output=True, text=True).stdout
        self.assertNotIn("notes.txt", shown)
