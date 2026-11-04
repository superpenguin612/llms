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


if __name__ == "__main__":
    unittest.main()
