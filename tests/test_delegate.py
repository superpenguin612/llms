"""contrib/delegate/delegate: a fake pi on PATH, real git repos in a temp folder, made-up data."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "contrib" / "delegate"

# Fake pi: behaves by model name. ok*: commit + result.md; fail*: exit 1, no result;
# err*: commit + result.md, then an API error; slow*: sleep past the timeout.
FAKE_PI = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, subprocess, sys, time
    from pathlib import Path
    a = sys.argv[1:]
    run = Path(os.environ["DELEGATE_RUN"])
    with open(run / "argv.jsonl", "a") as f:
        f.write(json.dumps({"argv": a, "cwd": os.getcwd(),
                            "stdin_null": os.path.samefile("/proc/self/fd/0", "/dev/null")}) + "\\n")
    model = a[a.index("--model") + 1]
    if model.startswith("tmpx"):
        Path("/tmp/delegate-private-tmp").write_text(model)
    if model.startswith("sig"):  # killed by hand, well before any timeout
        os.kill(os.getpid(), 9)
    if model.startswith("term"):  # stopped by hand with SIGTERM
        os.kill(os.getpid(), 15)
    if "--session" in a:
        session = Path(a[a.index("--session") + 1])
    else:
        sd = Path(a[a.index("--session-dir") + 1])
        sd.mkdir(parents=True, exist_ok=True)
        session = sd / f"{time.time_ns()}.jsonl"
    def log(stop="stop", n=100):
        u = {"input": n, "output": n // 10, "cacheRead": 5, "cacheWrite": 1, "cost": {"total": 0.01}}
        with open(session, "a") as f:
            f.write(json.dumps({"type": "message", "message": {"role": "assistant", "usage": u, "stopReason": stop}}) + "\\n")
    def commit():
        name = f"{model}-{time.time_ns()}.txt"
        Path(name).write_text(model)
        subprocess.run(["git", "add", name], check=True)
        subprocess.run(["git", "commit", "-qm", f"test: {name}"], check=True)
    if model.startswith("slow"):
        time.sleep(30)
    if model.startswith("fail") and "--session" not in a:  # a continued session (retry) succeeds
        log()
        sys.exit(1)
    commit()
    log()
    log()
    (run / "result.md").write_text(f"status: done\\nby {model}\\n")
    if model.startswith("err"):
        Path("junk.txt").write_text("untracked")
        log("error", 0)
        print("429: quota exceeded", file=sys.stderr)
        sys.exit(1)
    if model == "probe":
        (run / "probe.json").write_text(json.dumps({"env": dict(os.environ),
            "creds": os.listdir(Path.home() / "creds"),
            "codex": (Path.home() / ".codex/auth.json").read_text()}))
""")


def sh(cwd, *args, env):
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def tempdir():
    """A temp dir outside /tmp: workers get a private tmpfs /tmp, so fixtures under the host
    /tmp (the fake pi on PATH, HOME, the repo) would not exist inside their sandbox."""
    for base in ("/var/tmp", Path.cwd() / "build"):
        try:
            base = Path(base).resolve()
            if base == Path("/tmp") or Path("/tmp") in base.parents:
                continue
            base.mkdir(parents=True, exist_ok=True)
            return tempfile.TemporaryDirectory(dir=base, prefix="delegate-test-")
        except OSError:
            continue
    return tempfile.TemporaryDirectory()


def load_delegate():
    from importlib.machinery import SourceFileLoader
    import importlib.util
    loader = SourceFileLoader("delegate_mod", str(SRC / "delegate"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("delegate_mod", loader))
    loader.exec_module(mod)
    return mod


def sandbox_works():
    """Probe bwrap once: some environments ship it but forbid user/PID namespaces."""
    if not shutil.which("bwrap"):
        return False, "needs bubblewrap"
    p = subprocess.run(["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "true"],
                       capture_output=True, text=True)
    if p.returncode:
        reason = p.stderr.strip().splitlines()[0] if p.stderr.strip() else f"exit {p.returncode}"
        return False, f"bwrap cannot create a user/PID namespace here ({reason})"
    return True, ""


SANDBOX, SANDBOX_SKIP = sandbox_works()


@unittest.skipUnless(SANDBOX, SANDBOX_SKIP)
class DelegateBase(unittest.TestCase):
    def setUp(self):
        td = tempdir()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / ".gitconfig").write_text("[user]\n\tname = Test\n\temail = test@example.com\n")
        (self.home / "creds").mkdir()
        (self.home / "creds" / "opencode-go.env").write_text("OPENCODE_API_KEY=fake-key\n")
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "pi").write_text(FAKE_PI)
        (bindir / "pi").chmod(0o755)
        # A copy of the tool, so each test can set its own chains.json.
        self.tool = self.tmp / "tool"
        shutil.copytree(SRC, self.tool)
        self.env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(self.home), "LANG": "C.UTF-8"}
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main", env=self.env)
        (self.repo / "a.txt").write_text("a\n")
        sh(self.repo, "git", "add", "a.txt", env=self.env)
        sh(self.repo, "git", "commit", "-qm", "init", env=self.env)
        self.base = sh(self.repo, "git", "rev-parse", "HEAD", env=self.env)
        self.brief = self.tmp / "brief.md"
        self.brief.write_text("Goal: add a file.\n")
        self.data = self.home / ".local/share/delegate"

    def chains(self, **chains):
        (self.tool / "chains.json").write_text(json.dumps(chains))

    def delegate(self, *args):
        return subprocess.run([sys.executable, str(self.tool / "delegate"), *args], cwd=self.repo,
                              env=self.env, capture_output=True, text=True, timeout=60)

    def run_task(self, task, *extra):
        return self.delegate("run", "--repo", str(self.repo), "--task", task, "--brief", str(self.brief), *extra)

    def records(self):
        return [json.loads(l) for l in (self.data / "runs.jsonl").read_text().splitlines()]

    def record(self, task):
        return next(r for r in self.records() if r["task"] == task)

    def calls(self, task):
        return [json.loads(l) for l in (self.data / "runs" / task / "argv.jsonl").read_text().splitlines()]

    def git(self, *args):
        return sh(self.repo, "git", *args, env=self.env)


class TestRun(DelegateBase):
    def test_ok_run_in_worktree(self):
        self.chains(implementation=["ok-a", "ok-b"])
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 0, p.stderr)
        wt = self.data / "worktrees" / "repo" / "t1"
        self.assertTrue((wt / "a.txt").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)  # the repo itself is untouched
        log = self.git("log", "--format=%s", "main..delegate/t1").splitlines()
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0].startswith("test: ok-a-"))
        run = self.data / "runs" / "t1"
        self.assertEqual((run / "brief.md").read_text(), "Goal: add a file.\n")
        self.assertFalse((run / "running").exists())
        [call] = self.calls("t1")
        self.assertEqual(Path(call["cwd"]), wt)
        self.assertTrue(call["stdin_null"])  # pi -p waits for EOF on an open stdin
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--provider") + 1], "opencode-go")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("--session-dir") + 1], str(run / "sessions"))
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], str((self.tool / "worker-rules.md").resolve()))
        self.assertNotIn(str((self.tool / "here-rules.md").resolve()), argv)  # a worktree commits
        self.assertEqual(argv[-1], "Goal: add a file.\n")
        r = self.record("t1")
        self.assertEqual((r["mode"], r["chain"], r["status"], r["outcome"]), ("worktree", "implementation", "ok", None))
        self.assertEqual((r["base_branch"], r["base_commit"], r["branch"]), ("main", self.base, "delegate/t1"))
        [att] = r["attempts"]
        self.assertEqual(att["model"], "ok-a")
        self.assertEqual(att["outcome"], "ok")
        # token sums from the session log: two assistant messages
        self.assertEqual((att["input"], att["output"], att["cache_read"], att["cache_write"]), (200, 20, 10, 2))
        self.assertAlmostEqual(att["cost"], 0.02)
        self.assertIn("seconds", att)
        self.assertIn("ok", p.stdout)
        self.assertIn("by ok-a", p.stdout)  # first lines of result.md

    def test_failed(self):
        self.chains(implementation=["fail-a"])
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.record("t1")["attempts"][0]["outcome"], "failed")
        self.assertEqual(self.record("t1")["status"], "failed")

    def test_api_error(self):
        self.chains(implementation=["err-a"])
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.record("t1")["status"], "api_error")

    def test_timeout(self):
        self.chains(implementation=["slow-a"])
        p = self.run_task("t1", "--timeout", "1s")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.record("t1")["status"], "timeout")

    def test_killed_worker_stops_the_chain(self):
        self.chains(implementation=["sig-a", "ok-b"])
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 1)
        r = self.record("t1")
        self.assertEqual([(a["model"], a["outcome"]) for a in r["attempts"]], [("sig-a", "killed")])
        self.assertEqual(r["status"], "killed")

    def test_sigterm_to_delegate_records_killed(self):
        self.chains(implementation=["slow-a", "ok-b"])
        p = subprocess.Popen([sys.executable, str(self.tool / "delegate"), "run", "--repo", str(self.repo),
                              "--task", "t1", "--brief", str(self.brief)],
                             cwd=self.repo, env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        argv = self.data / "runs" / "t1" / "argv.jsonl"
        deadline = time.time() + 30
        while not argv.exists() and time.time() < deadline:
            time.sleep(0.05)
        p.terminate()
        p.communicate(timeout=30)
        self.assertNotEqual(p.returncode, 0)
        r = self.record("t1")
        self.assertEqual([(a["model"], a["outcome"]) for a in r["attempts"]], [("slow-a", "killed")])
        self.assertEqual(r["status"], "killed")
        self.assertFalse((self.data / "runs" / "t1" / "running").exists())

    def test_status(self):
        self.chains(implementation=["ok-a"])
        self.run_task("t1")
        p = self.delegate("status")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout, r"t1\s+ok\s+ok-a")
        p = self.delegate("status", "t1")
        self.assertEqual(json.loads(p.stdout)["task"], "t1")

    def test_refuses_fourth_live_run(self):
        self.chains(implementation=["ok-a"])
        for t in ("x1", "x2", "x3"):
            (self.data / "runs" / t).mkdir(parents=True)
            (self.data / "runs" / t / "running").write_text(str(os.getpid()))
        p = self.run_task("t1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("3 runs", p.stderr)
        dead = subprocess.Popen(["true"])
        dead.wait()
        (self.data / "runs" / "x3" / "running").write_text(str(dead.pid))
        self.assertEqual(self.run_task("t1").returncode, 0)

    def test_refuses_reused_task(self):
        self.chains(implementation=["ok-a"])
        self.run_task("t1")
        p = self.run_task("t1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("exists", p.stderr)

    def test_refuses_detached_head(self):
        self.chains(implementation=["ok-a"])
        self.git("checkout", "-q", "--detach")
        p = self.run_task("t1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("detached HEAD", p.stderr)
        self.assertIn("check out a branch", p.stderr)
        self.assertFalse((self.data / "runs" / "t1").exists())
        self.assertFalse((self.data / "worktrees").exists())

    def test_empty_chain_errors(self):
        self.chains(implementation=["ok-a"], bulk=[])
        p = self.run_task("t1", "--chain", "bulk")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("empty", p.stderr)
        self.assertFalse((self.data / "worktrees").exists())

    def test_shipped_chains(self):
        chains = json.loads((SRC / "chains.json").read_text())
        self.assertEqual(chains["implementation"], ["deepseek-v4.1-flash"])
        self.assertEqual(chains["bulk"], ["deepseek-v4.1-flash"])

    def test_failed_start_leaves_nothing_behind(self):
        self.chains(implementation=["ok-a"])
        self.git("branch", "delegate/t1")  # makes git worktree add -b fail
        p = self.run_task("t1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("worktree add", p.stderr)
        self.assertFalse((self.data / "runs" / "t1").exists())
        self.git("branch", "-D", "delegate/t1")
        self.assertEqual(self.run_task("t1").returncode, 0)  # the name can be used again


class TestFallback(DelegateBase):
    def outcomes(self, task):
        return [(a["model"], a["outcome"]) for a in self.record(task)["attempts"]]

    def test_api_error_falls_back_keeping_commits_on_backup_branch(self):
        self.chains(implementation=["err-a", "ok-b"])
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.outcomes("t1"), [("err-a", "api_error"), ("ok-b", "ok")])
        backup = self.git("log", "--format=%s", "main..delegate/t1@err-a").splitlines()
        self.assertEqual(len(backup), 1)
        self.assertIn("err-a", backup[0])
        kept = self.git("log", "--format=%s", "main..delegate/t1").splitlines()
        self.assertEqual(len(kept), 1)
        self.assertIn("ok-b", kept[0])
        self.assertFalse((self.data / "worktrees/repo/t1/junk.txt").exists())
        self.assertIn("by ok-b", (self.data / "runs/t1/result.md").read_text())
        self.assertEqual(self.record("t1")["status"], "ok")

    def test_timeout_falls_back(self):
        self.chains(implementation=["slow-a", "ok-b"])
        p = self.run_task("t1", "--timeout", "1s")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.outcomes("t1"), [("slow-a", "timeout"), ("ok-b", "ok")])
        self.assertEqual(self.git("branch", "--list", "delegate/t1@*"), "")  # no commits, no backup

    def test_failed_stops_the_chain(self):
        self.chains(implementation=["fail-a", "ok-b"])
        self.assertEqual(self.run_task("t1").returncode, 1)
        self.assertEqual(self.outcomes("t1"), [("fail-a", "failed")])

    def test_killed_does_not_fall_back(self):
        self.chains(implementation=["sig-a", "ok-b"])
        self.assertEqual(self.run_task("t1").returncode, 1)
        self.assertEqual(self.outcomes("t1"), [("sig-a", "killed")])

    def test_worker_sigterm_is_killed(self):
        self.chains(implementation=["term-a", "ok-b"])
        self.assertEqual(self.run_task("t1").returncode, 1)
        self.assertEqual(self.outcomes("t1"), [("term-a", "killed")])

    def test_result_removed_before_next_model(self):
        self.chains(implementation=["err-a", "fail-b"])
        self.assertEqual(self.run_task("t1").returncode, 1)
        self.assertEqual(self.outcomes("t1"), [("err-a", "api_error"), ("fail-b", "failed")])
        self.assertFalse((self.data / "runs/t1/result.md").exists())


class TestRetryCleanHere(DelegateBase):
    def test_retry_continues_the_session_once(self):
        self.chains(implementation=["fail-a"])
        self.run_task("t1")
        session = self.record("t1")["attempts"][0]["session"]
        fb = self.tmp / "fb.md"
        fb.write_text("Also handle empty input.\n")
        p = self.delegate("retry", "t1", "--feedback", str(fb))
        self.assertEqual(p.returncode, 0, p.stderr)
        argv = self.calls("t1")[-1]["argv"]
        self.assertEqual(argv[argv.index("--session") + 1], session)
        self.assertEqual(argv[argv.index("--model") + 1], "fail-a")
        self.assertEqual(argv[-1], "Also handle empty input.\n")
        r = self.record("t1")
        self.assertEqual(r["status"], "ok")
        att = r["attempts"][-1]
        self.assertTrue(att["retry"])
        self.assertEqual(att["input"], 200)  # only the messages the retry added
        p = self.delegate("retry", "t1", "--feedback", str(fb))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("already retried", p.stderr)

    def test_clean_merged(self):
        self.chains(implementation=["ok-a"])
        self.run_task("t1")
        self.git("merge", "-q", "--ff-only", "delegate/t1")
        p = self.delegate("clean", "t1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.record("t1")["outcome"], "merged")
        self.assertFalse((self.data / "worktrees/repo/t1").exists())
        self.assertEqual(self.git("branch", "--list", "delegate/*"), "")
        self.assertTrue((self.data / "runs/t1/result.md").exists())  # run folders are kept
        self.assertNotEqual(self.delegate("clean", "t1").returncode, 0)  # already cleaned

    def test_clean_unmerged_needs_force(self):
        self.chains(implementation=["err-a", "ok-b"])
        self.run_task("t1")
        p = self.delegate("clean", "t1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--force", p.stderr)
        self.assertIsNone(self.record("t1")["outcome"])
        self.assertEqual(self.delegate("clean", "t1", "--force").returncode, 0)
        self.assertEqual(self.record("t1")["outcome"], "abandoned")
        self.assertEqual(self.git("branch", "--list", "delegate/*"), "")  # backup branch too

    def test_clean_opus(self):
        self.chains(implementation=["ok-a"])
        self.run_task("t1")
        self.assertEqual(self.delegate("clean", "t1", "--opus").returncode, 0)
        self.assertEqual(self.record("t1")["outcome"], "opus")

    def test_here_runs_in_place(self):
        self.chains(implementation=["ok-a"])
        p = self.run_task("t1", "--here")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.data / "worktrees").exists())
        self.assertEqual(Path(self.calls("t1")[0]["cwd"]), self.repo)
        argv = self.calls("t1")[0]["argv"]
        appends = [argv[i + 1] for i, x in enumerate(argv) if x == "--append-system-prompt"]
        self.assertEqual(appends, [str((self.tool / "worker-rules.md").resolve()),
                                   str((self.tool / "here-rules.md").resolve())])
        self.assertEqual(len(self.git("log", "--format=%s", f"{self.base}..main").splitlines()), 1)
        r = self.record("t1")
        self.assertEqual((r["mode"], r["worktree"], r["branch"]), ("here", None, None))
        self.assertEqual(self.delegate("clean", "t1").returncode, 0)
        self.assertEqual(self.record("t1")["outcome"], "merged")


class TestSandbox(DelegateBase):
    def test_tmp_is_private(self):
        self.chains(implementation=["tmpx-a"])
        marker = Path("/tmp/delegate-private-tmp")
        marker.unlink(missing_ok=True)
        self.addCleanup(lambda: marker.unlink(missing_ok=True))
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(marker.exists())  # the worker's /tmp is a private tmpfs

    def test_worker_sees_allowlisted_env_and_no_credentials(self):
        self.chains(implementation=["probe"])
        (self.home / ".codex").mkdir()
        (self.home / ".codex/auth.json").write_text("secret")
        (self.home / "creds" / "other").write_text("secret")
        self.env["SSH_AUTH_SOCK"] = "/tmp/agent.sock"
        self.env["GH_TOKEN"] = "secret"
        p = self.run_task("t1")
        self.assertEqual(p.returncode, 0, p.stderr)
        probe = json.loads((self.data / "runs/t1/probe.json").read_text())
        allowed = {"PATH", "HOME", "LANG", "OPENCODE_API_KEY", "DELEGATE_RUN", "TMPDIR",
                   "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"}
        self.assertLessEqual(set(probe["env"]) - {"PWD", "SHLVL", "_", "LC_CTYPE"}, allowed)
        self.assertEqual(probe["env"]["OPENCODE_API_KEY"], "fake-key")
        self.assertEqual(probe["env"]["TMPDIR"], "/tmp")  # the private /tmp, never the host's TMPDIR
        self.assertEqual(probe["creds"], [])
        self.assertEqual(probe["codex"], "")

    def test_missing_key_errors(self):
        self.chains(implementation=["ok-a"])
        (self.home / "creds" / "opencode-go.env").unlink()
        p = self.run_task("t1")
        self.assertIn("OPENCODE_API_KEY", p.stderr)
        self.assertFalse((self.data / "worktrees").exists())


@unittest.skipUnless(shutil.which("bwrap"), "needs bubblewrap")
class TestSandboxArgv(unittest.TestCase):
    """The bwrap argv itself: a private /tmp, with the worker's own paths bound back after it."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.mod = load_delegate()
        self.mod.DATA = Path(td.name)

    def test_private_tmp_and_binds_under_it(self):
        argv = self.mod.sandbox("/tmp/delegate/work", "/tmp/delegate/run", "k", ["true"])
        i = next(i for i, x in enumerate(argv) if x == "--tmpfs" and argv[i + 1] == "/tmp")
        self.assertEqual(argv[i + 2:i + 8], ["--bind", "/tmp/delegate/work", "/tmp/delegate/work",
                                            "--bind", "/tmp/delegate/run", "/tmp/delegate/run"])

    def test_repo_under_tmp_is_bound_for_a_worktree(self):
        argv = self.mod.sandbox("/work", "/runs", "k", ["true"], repo="/tmp/demo")
        i = next(i for i, x in enumerate(argv) if x == "--tmpfs" and argv[i + 1] == "/tmp")
        self.assertEqual(argv[i + 2:i + 5], ["--bind", "/tmp/demo", "/tmp/demo"])

    def test_no_binds_when_nothing_is_under_tmp(self):
        argv = self.mod.sandbox("/work", "/runs", "k", ["true"])
        i = next(i for i, x in enumerate(argv) if x == "--tmpfs" and argv[i + 1] == "/tmp")
        self.assertEqual(argv[i + 2], "--unshare-pid")
        self.assertNotIn("--bind", argv[i:])


@unittest.skipUnless(SANDBOX and (Path.home() / "creds/opencode-go.env").exists(),
                     SANDBOX_SKIP or "needs this machine's ~/creds")
class TestRealHomeSandbox(unittest.TestCase):
    """The sandbox against this machine's real home: credentials hidden, commits still work."""

    def test_real_credentials_hidden(self):
        mod = load_delegate()
        home = Path.home()
        with tempfile.TemporaryDirectory() as td:
            mod.DATA = Path(td) / "data"
            script = textwrap.dedent(f"""\
                set -e
                for d in ~/creds ~/.ssh ~/.config/gh; do [ ! -e "$d" ] || ls -A "$d" | wc -l; done
                for f in ~/.claude/.credentials.json ~/.codex/auth.json ~/.pi/agent/auth.json; do
                    [ ! -e "$f" ] || wc -c < "$f"; done
                echo ENV; env | cut -d= -f1 | sort
                cd {td} && git init -q r && cd r && git commit -q --allow-empty -m "test: sandbox" && echo COMMIT
                """)
            p = subprocess.run(mod.sandbox(td, td, "k", ["sh", "-c", script]), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        files, _, rest = p.stdout.partition("ENV\n")
        self.assertEqual(set(files.split()), {"0"})  # hidden dirs list empty, hidden files read empty
        names = set(rest.split()) - {"COMMIT", "PWD", "SHLVL", "_"}
        self.assertLessEqual(names, set(mod.ALLOW) | {"OPENCODE_API_KEY", "DELEGATE_RUN", "TMPDIR", "GIT_CONFIG_COUNT",
                                                      "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"})
        self.assertIn("COMMIT", rest)  # commit works without the hidden signing key
        self.assertTrue((home / "creds").exists())


if __name__ == "__main__":
    unittest.main()
