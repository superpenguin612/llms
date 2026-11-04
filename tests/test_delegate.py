"""contrib/delegate/delegate: a fake pi on PATH, real git repos in a temp folder, made-up data."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
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
        f.write(json.dumps({"argv": a, "cwd": os.getcwd()}) + "\\n")
    model = a[a.index("--model") + 1]
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
    if model.startswith("fail"):
        log()
        sys.exit(1)
    commit()
    log()
    log()
    (run / "result.md").write_text(f"status: done\\nby {model}\\n")
    if model.startswith("err"):
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


class DelegateBase(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
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
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--provider") + 1], "opencode-go")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("--session-dir") + 1], str(run / "sessions"))
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], str((self.tool / "worker-rules.md").resolve()))
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

    def test_empty_chain_errors(self):
        self.chains(implementation=["ok-a"], bulk=[])
        p = self.run_task("t1", "--chain", "bulk")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("empty", p.stderr)
        self.assertFalse((self.data / "worktrees").exists())

    def test_shipped_chains(self):
        chains = json.loads((SRC / "chains.json").read_text())
        self.assertEqual(chains["implementation"], ["deepseek-v4.1-flash", "kimi-k2.7-code"])
        self.assertEqual(chains["bulk"], [])


if __name__ == "__main__":
    unittest.main()
