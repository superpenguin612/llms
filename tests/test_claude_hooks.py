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
