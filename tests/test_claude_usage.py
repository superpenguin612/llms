"""contrib/claude/claude-usage: made-up transcripts only."""

import datetime as dt
import importlib.util
import json
import re
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "contrib" / "claude" / "claude-usage"
_loader = SourceFileLoader("claude_usage", str(SCRIPT))
cu = importlib.util.module_from_spec(importlib.util.spec_from_loader("claude_usage", _loader))
_loader.exec_module(cu)


def turn(mid, ts, model="claude-test", cr=0, cw1=0, cw5=0, out=10, blocks=1, read=None):
    """One assistant reply logged once per content block, as Claude Code does."""
    usage = {"input_tokens": 5, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw1 + cw5,
             "cache_creation": {"ephemeral_1h_input_tokens": cw1, "ephemeral_5m_input_tokens": cw5},
             "output_tokens": out}
    lines = []
    for i in range(blocks):
        block = {"type": "text", "text": "ok"}
        if read and i == 0:
            block = {"type": "tool_use", "id": f"{mid}-t", "name": "Read", "input": {"file_path": read}}
        lines.append({"type": "assistant", "timestamp": ts,
                      "message": {"id": mid, "model": model, "content": [block], "usage": usage}})
    return lines


def cost(total, sid="s1"):
    return {"type": "cost-state", "sessionId": sid, "totalCostUSD": total,
            "modelUsage": {"claude-test": {"costUSD": total}}}


def bash_turn(mid, ts, command):
    """One assistant line with a Bash tool call and no usage (the command itself is what matters)."""
    return [{"type": "assistant", "timestamp": ts,
             "message": {"id": mid, "model": "claude-test",
                         "content": [{"type": "tool_use", "id": f"{mid}-t", "name": "Bash",
                                      "input": {"command": command}}]}}]


def attempt(model, outcome, input=0, output=0, cache_read=0, cache_write=0, cost=0.0, retry=False):
    a = {"model": model, "outcome": outcome, "exit": 0, "input": input, "output": output,
         "cache_read": cache_read, "cache_write": cache_write, "cost": cost, "seconds": 1, "session": None}
    if retry:
        a["retry"] = True
    return a


def delegate_run(task, started, outcome="merged", chain="implementation", attempts=()):
    return {"task": task, "mode": "worktree", "chain": chain, "started": started, "status": "ok",
            "outcome": outcome, "repo": "/tmp/demo", "attempts": list(attempts)}


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


class TestClaudeUsage(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name) / "home" / "someone"
        patch = mock.patch.object(cu, "HOME", self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.proj = self.home / ".claude" / "projects" / (re.sub(r"[^A-Za-z0-9]", "-", str(self.home)) + "-src-demo")

    def test_usage_deduped_by_message_id(self):
        f = self.home / "t.jsonl"
        write(f, [{"type": "user", "message": {"content": "hello there"}},
                  *turn("m1", "2026-11-02T10:00:00Z", cr=100, out=7, blocks=3, read="/x/a.py"),
                  *turn("m2", "2026-11-02T10:01:00Z", cr=200, out=3, read="/x/a.py")])
        s = cu.parse(f)
        self.assertEqual(len(s["turns"]), 2)
        self.assertEqual(sum(t["c"]["out"] for t in s["turns"]), 10)
        self.assertEqual(sum(t["c"]["cr"] for t in s["turns"]), 300)
        self.assertEqual(s["first"], "hello there")
        self.assertEqual(s["repeats"], {"/x/a.py": 1})

    def test_cache_creation_split_and_fallback(self):
        f = self.home / "t.jsonl"
        old = {"type": "assistant", "timestamp": "2026-11-02T10:00:00Z",
               "message": {"id": "m", "model": "x", "usage": {"cache_creation_input_tokens": 40}}}
        write(f, [*turn("m1", "2026-11-02T10:00:00Z", cw1=30, cw5=4), old])
        c1, c2 = (t["c"] for t in cu.parse(f)["turns"])
        self.assertEqual((c1["cw1"], c1["cw5"], c2["cw1"], c2["cw5"]), (30, 4, 0, 40))

    def test_cost_state_last_wins_missing_and_live(self):
        write(self.proj / "s1.jsonl", [*turn("a", "2026-11-02T10:00:00Z"), cost(1.0), cost(2.5)])
        write(self.proj / "s2.jsonl", turn("b", "2026-11-02T10:00:00Z"))
        write(self.proj / "s3.jsonl", [cost(9.0), *turn("c", "2026-11-02T10:00:00Z")])  # still running
        got = {s["id"]: s["cost"] for s in cu.load(30)}
        self.assertEqual(got, {"s1": 2.5, "s2": None, "s3": None})
        (self.home / ".cache" / "claude-statusline").mkdir(parents=True)
        (self.home / ".cache" / "claude-statusline" / "s3.json").write_text('{"cost": 4.0}')  # saved by the status line
        got = {s["id"]: s["cost"] for s in cu.load(30)}
        self.assertEqual(got, {"s1": 2.5, "s2": None, "s3": 4.0})

    def test_miss_classification(self):
        def t(sec, model="a", cw=50000, cr=1000):
            return {"t": sec, "model": model, "c": {"cw1": cw, "cw5": 0, "cr": cr}}
        turns = [t(0), t(10), t(20, model="b"), t(20 + 7200, model="b"), t(7220 + 600, model="b"),
                 t(7820 + 5, model="b", cr=90000), t(7830 + 5, model="b", cw=1000)]
        self.assertEqual(cu.misses(turns), [("other", 50000), ("model switch", 50000),
                                            ("idle >1h", 50000), ("idle 5-60m", 50000)])

    def test_project_label(self):
        home = re.sub(r"[^A-Za-z0-9]", "-", str(self.home))
        self.assertEqual(cu.project_label(home + "-src-demo"), "src-demo")
        self.assertEqual(cu.project_label(home), "~")
        self.assertEqual(cu.project_label("-tmp-x"), "-tmp-x")

    def test_week_file(self):
        self.assertEqual(cu.week_file(dt.datetime(2026, 11, 9, 6)), "2026-W45.md")  # Monday: week just ended
        self.assertEqual(cu.week_file(dt.datetime(2026, 11, 11)), "2026-W46.md")
        self.assertEqual(cu.week_file(dt.datetime(2027, 1, 4, 6)), "2026-W53.md")  # ISO year, not calendar

    def test_report_end_to_end(self):
        write(self.proj / "s1.jsonl", [{"type": "ai-title", "aiTitle": "Demo work"},
                                       {"type": "user", "message": {"content": "build the thing"}},
                                       *turn("a", "2026-11-02T10:00:00Z", cr=300000, out=100),
                                       *turn("b", "2026-11-02T10:01:00Z", cr=10, out=100), cost(4.0)])
        sub = self.proj / "s1" / "subagents" / "agent-1.jsonl"
        write(sub, turn("c", "2026-11-02T10:02:00Z", model="claude-opus-x", out=100))
        sub.with_name("agent-1.meta.json").write_text(json.dumps({"agentType": "general-purpose",
                                                                  "description": "Do a part"}))
        write(self.home / ".codex" / "sessions" / "2026" / "r.jsonl",
              [{"type": "session_meta", "payload": {"cwd": str(self.home / "src" / "demo")}},
               {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                   "input_tokens": 2_000_000, "cached_input_tokens": 1_000_000, "output_tokens": 50_000}}}}])
        out = self.home / "out"
        with mock.patch("sys.stdout"):
            cu.main(["report", "--out", str(out)])
        text = next(out.glob("*-W*.md")).read_text()
        self.assertIn("| src-demo | 1 | $4.00 | 100% |", text)
        self.assertIn("Demo work | build the thing | 2 | 300k |", text)
        self.assertIn("Opus subagents: 1.", text)
        self.assertIn("| general-purpose | Do a part | 1 |", text)
        self.assertIn("| src/demo | 1 | 2.0M | 1.0M | 0.1M |", text)

    def test_delegate_tasks_from_commands(self):
        cmd = ("cd x && delegate run --repo . --task one --brief b.md && "
               "python3 tool/delegate run --task two --brief b.md && "
               "/usr/local/bin/delegate retry three --feedback f.md")
        self.assertEqual(cu.delegate_tasks(cmd), {"one", "two", "three"})
        self.assertEqual(cu.delegate_tasks("delegate run --help"), set())

    def test_report_delegated_by_model_and_flags(self):
        started = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
        write(self.home / ".local/share/delegate/runs.jsonl", [
            delegate_run("fallback-task", started, attempts=[
                attempt("m1", "api_error", input=100, output=10, cache_read=20, cost=0.1),
                attempt("m2", "ok", input=200, output=20, cache_read=30, cache_write=40, cost=0.2),
                attempt("m2", "ok", input=300, output=30, cache_read=50, cache_write=60, cost=0.3, retry=True),
            ]),
            delegate_run("timeout-task", started, outcome="abandoned", chain="slow", attempts=[
                attempt("m2", "timeout", input=400, output=40, cost=0.4),
            ]),
        ])
        out = self.home / "out"
        with mock.patch("sys.stdout"):
            cu.main(["report", "--out", str(out)])
        text = next(out.glob("*-W*.md")).read_text()
        self.assertIn("| m1 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 100 | 10 | 20 | 0 | $0.10 |", text)
        self.assertIn("| m2 | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 900 | 90 | 80 | 100 | $0.90 |", text)
        self.assertIn("| fallback-task | implementation | merged | 3 | yes | yes | $0.60 | — |", text)
        self.assertIn("| timeout-task | slow | abandoned | 1 | no | no | $0.40 | — |", text)

    def test_report_delegated_takeovers(self):
        started = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
        write(self.home / ".local/share/delegate/runs.jsonl", [
            delegate_run("retry-task", started, attempts=[
                attempt("m1", "timeout", input=100, output=10, cost=0.1),
                attempt("m2", "ok", input=200, output=20, cost=0.2),
            ]),
            # Opus took this one over: counted against the last attempt's model.
            delegate_run("takeover-task", started, outcome="opus", attempts=[
                attempt("m1", "failed", input=300, output=30, cost=0.3),
                attempt("m2", "ok", input=400, output=40, cost=0.4),
            ]),
            delegate_run("plain-task", started, attempts=[
                attempt("m2", "ok", input=500, output=50, cost=0.5),
            ]),
        ])
        out = self.home / "out"
        with mock.patch("sys.stdout"):
            cu.main(["report", "--out", str(out)])
        text = next(out.glob("*-W*.md")).read_text()
        self.assertIn("| m1 | 2 | 0 | 1 | 0 | 1 | 0 | 0 | 400 | 40 | 0 | 0 | $0.40 |", text)
        self.assertIn("| m2 | 3 | 3 | 0 | 0 | 0 | 0 | 1 | 1.1k | 110 | 0 | 0 | $1.10 |", text)
        self.assertIn("Takeovers: 1 of 3 runs", text)

    def test_report_delegated_links_session(self):
        now = dt.datetime.now(dt.timezone.utc)
        started = now.astimezone().isoformat(timespec="seconds")
        ts = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        write(self.proj / "s1.jsonl", [
            {"type": "ai-title", "aiTitle": "Demo session"},
            *turn("a", ts, cr=1000, out=100),
            *bash_turn("b", ts, "cd ~/src/demo && python3 /opt/tools/delegate run --repo . --task X --brief b.md"),
            cost(1.5),
        ])
        write(self.home / ".local/share/delegate/runs.jsonl", [
            delegate_run("X", started, attempts=[attempt("m1", "ok", input=10, output=5, cache_read=3,
                                                         cache_write=2, cost=0.01)]),
        ])
        out = self.home / "out"
        with mock.patch("sys.stdout"):
            cu.main(["report", "--out", str(out)])
        text = next(out.glob("*-W*.md")).read_text()
        self.assertIn("| X | implementation | merged | 1 | no | no | $0.01 | src-demo: s1 |", text)
        self.assertIn("| src-demo: s1 | $1.50 | 1.1k | 1 | 20 | $0.01 |", text)
        self.assertIn("| total | $1.50 | 1.1k | 1 | 20 | $0.01 |", text)

    def test_report_delegated_empty_window(self):
        old = (dt.datetime.now(dt.timezone.utc).astimezone()
               - dt.timedelta(days=30)).isoformat(timespec="seconds")
        write(self.home / ".local/share/delegate/runs.jsonl", [delegate_run("old-task", old, outcome=None)])
        out = self.home / "out"
        with mock.patch("sys.stdout"):
            cu.main(["report", "--days", "7", "--out", str(out)])
        text = next(out.glob("*-W*.md")).read_text()
        self.assertIn("No delegate runs started in the last 7 days.", text)
        self.assertNotIn("### By model", text)


if __name__ == "__main__":
    unittest.main()
