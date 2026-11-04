import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "contrib" / "claude" / "hooks"


class HookCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = dict(os.environ, HOME=str(self.tmp))

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, name, payload):
        cmd = [str(HOOKS / name)]
        r = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, env=self.env)
        return r.returncode, r.stdout, r.stderr


class TestContextNotice(HookCase):
    def transcript(self, ctx):
        p = self.tmp / "t.jsonl"
        usage = {"input_tokens": 1, "cache_read_input_tokens": ctx - 1, "cache_creation_input_tokens": 0}
        with open(p, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"usage": usage}}) + "\n")
        return p

    def notice(self, ctx):
        _, out, _ = self.run_hook("context-notice.py", {"session_id": "s", "transcript_path": str(self.transcript(ctx))})
        return json.loads(out)["systemMessage"] if out.strip() else None

    def test_fires_once_per_crossing_and_rearms(self):
        self.assertIsNone(self.notice(100000))
        self.assertEqual(self.notice(260000), "Context window has reached 250k tokens.")
        self.assertIsNone(self.notice(270000))
        self.assertEqual(self.notice(410000), "Context window has reached 400k tokens.")
        self.assertIsNone(self.notice(420000))
        self.assertIsNone(self.notice(50000))  # compacted: re-armed
        self.assertEqual(self.notice(255000), "Context window has reached 250k tokens.")

    def test_jump_past_both_reports_highest(self):
        self.assertEqual(self.notice(500000), "Context window has reached 400k tokens.")

    def test_sidechain_ignored(self):
        p = self.transcript(300000)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "assistant", "isSidechain": True,
                                "message": {"usage": {"input_tokens": 10}}}) + "\n")
        _, out, _ = self.run_hook("context-notice.py", {"session_id": "s", "transcript_path": str(p)})
        self.assertIn("250k", out)


class TestReadGuard(HookCase):
    def read(self, path, **extra):
        _, out, _ = self.run_hook("read-guard.py", {"session_id": "s", "hook_event_name": "PreToolUse",
                                                    "tool_input": dict(file_path=str(path), **extra)})
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else None

    def test_unchanged_reread_warns_changed_does_not(self):
        f = self.tmp / "a.txt"
        f.write_text("hello")
        self.assertIsNone(self.read(f))
        self.assertIn("unchanged", self.read(f))
        f.write_text("hello again")
        self.assertIsNone(self.read(f))

    def test_ranges_and_images_skipped(self):
        f = self.tmp / "a.txt"
        f.write_text("x")
        self.read(f)
        self.assertIsNone(self.read(f, offset=10))
        img = self.tmp / "big.png"
        img.write_bytes(b"0" * 200_000)
        self.assertIsNone(self.read(img))

    def test_big_file_warns(self):
        f = self.tmp / "big.log"
        f.write_text("x" * 200_000)
        self.assertIn("KB", self.read(f))

    def test_compact_resets(self):
        f = self.tmp / "a.txt"
        f.write_text("x")
        self.read(f)
        self.run_hook("read-guard.py", {"session_id": "s", "hook_event_name": "SessionStart", "source": "compact"})
        self.assertIsNone(self.read(f))


class TestHandoffLoader(HookCase):
    def setUp(self):
        super().setUp()
        self.dir = self.tmp / ".local/state/claude-handoffs"
        self.dir.mkdir(parents=True)
        (self.dir / "h.md").write_text("## Goal\nship it")
        self.ptr = self.dir / "pending-work-repo"
        self.ptr.write_text("~/.local/state/claude-handoffs/h.md")

    def load(self):
        _, out, _ = self.run_hook("handoff-loader.py", {"cwd": "/work/repo", "source": "clear"})
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else None

    def test_loads_once(self):
        self.assertIn("ship it", self.load())
        self.assertFalse(self.ptr.exists())
        self.assertIsNone(self.load())

    def test_stale_pointer_ignored(self):
        old = time.time() - 3 * 3600
        os.utime(self.ptr, (old, old))
        self.assertIsNone(self.load())
        self.assertFalse(self.ptr.exists())


class TestAgentGuard(HookCase):
    def code(self, **tool_input):
        tool_input.setdefault("prompt", "x")
        return self.run_hook("guard-agent-model.sh", {"tool_input": tool_input})[0]

    def test_rules(self):
        self.assertEqual(self.code(subagent_type="fork"), 2)
        self.assertEqual(self.code(subagent_type="fork", prompt="[fork: needs the thread] x"), 0)
        self.assertEqual(self.code(model="opus"), 0)
        self.assertEqual(self.code(model="sonnet"), 0)
        self.assertEqual(self.code(model="haiku", effort="xhigh"), 0)
        self.assertEqual(self.code(model="haiku"), 2)
        self.assertEqual(self.code(model="fable"), 2)
        self.assertEqual(self.code(prompt="[not-sonnet: reason] x"), 0)


class TestCompactInstructions(HookCase):
    def test_prints_checklist(self):
        _, out, _ = self.run_hook("compact-instructions.sh", {"trigger": "manual"})
        self.assertTrue(out.startswith("## Checklist"))
        self.assertIn("Next steps", out)


class TestStatusLine(HookCase):
    def line(self, **data):
        data.setdefault("model", {"display_name": "Opus 5.5"})
        cmd = ["python3", str(HOOKS.parent / "statusline.py")]  # settings.json runs it the same way
        out = subprocess.run(cmd, input=json.dumps(data), capture_output=True, text=True, env=self.env).stdout
        return __import__("re").sub(r"\x1b\[[0-9;]*m", "", out)

    def test_cache_context_and_extras(self):
        now = time.time()
        usage = {"input_tokens": 5, "cache_read_input_tokens": 300000, "cache_creation_input_tokens": 12000}
        out = self.line(session_id="s", effort={"level": "high"},
                        context_window={"current_usage": usage},
                        prompt_cache={"warm": True, "expires_at": now + 2520, "hit_ratio": 0.98, "misses": 2,
                                      "last_miss_cause": {"causes": ["model_switch"]}, "recache_tokens_if_cold": 312000},
                        pr={"number": 12, "review_state": "approved"},
                        cost={"total_cost_usd": 41.2, "total_lines_added": 120, "total_lines_removed": 30})
        self.assertTrue(out.startswith("Opus 5.5 high"))
        for part in ("ctx 312k", "cache 41m", "hit 98%", "2 misses: model switch", "#12 approved", "+120/-30", "$41.20"):
            self.assertIn(part, out)
        saved = json.loads((self.tmp / ".cache/claude-statusline/s.json").read_text())
        self.assertEqual(saved, {"cost": 41.2})

    def test_cache_nearly_cold_and_cold(self):
        now = time.time()
        pc = {"warm": True, "expires_at": now + 200, "recache_tokens_if_cold": 312000}
        cw = {"current_usage": {"input_tokens": 5, "cache_read_input_tokens": 312000}}
        self.assertIn("cache 3m (cold: 312k)", self.line(context_window=cw, prompt_cache=pc))
        self.assertIn("cache cold: 312k", self.line(context_window=cw, prompt_cache=dict(pc, expires_at=now - 5)))

    def test_no_cache_segment_after_compaction(self):
        pc = {"warm": False, "expires_at": time.time() - 5, "recache_tokens_if_cold": 312000, "hit_ratio": 0.9}
        out = self.line(context_window={"current_usage": None}, prompt_cache=pc)
        self.assertNotIn("cache", out)
        self.assertIn("hit 90%", out)

    def test_missing_fields(self):
        self.assertEqual(self.line(), "Opus 5.5")
        self.assertEqual(self.line(context_window={"current_usage": None}, prompt_cache=None), "Opus 5.5")
