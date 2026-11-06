import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

RUN = Path(__file__).resolve().parent.parent / "contrib" / "claude" / "mods" / "idle" / "eval" / "run.py"
SPEC = importlib.util.spec_from_file_location("idle_eval", RUN)
idle_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(idle_eval)


def record(**fields):
    return fields


class TranscriptCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_jsonl(self, records):
        path = self.tmp / "session-1.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return path


class TestReadCompaction(TranscriptCase):
    def records(self):
        summary = idle_eval.MOD_PREFIX + (
            " covers the earlier portion of the conversation.\n\nSummary:\nUse the blue door."
        )
        return [
            record(type="user", uuid="old-user", cwd="/work", message={"role": "user", "content": "old request"}),
            record(type="assistant", uuid="old-assistant",
                   message={"role": "assistant", "content": [{"type": "text", "text": "old answer"}]}),
            record(type="system", subtype="compact_boundary", uuid="boundary",
                   compactMetadata={"preservedMessages": {"uuids": ["keep-user", "keep-assistant", "keep-result"]}}),
            record(type="user", uuid="summary", isCompactSummary=True,
                   message={"role": "user", "content": summary}),
            record(type="user", uuid="keep-user",
                   message={"role": "user", "content": "keep this request"}),
            record(type="assistant", uuid="keep-assistant", message={"role": "assistant", "content": [
                {"type": "text", "text": "keep this answer"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/example"}},
            ]}),
            record(type="user", uuid="keep-result", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "R" * 600},
            ]}),
            record(type="assistant", uuid="dropped",
                   message={"role": "assistant", "content": [{"type": "text", "text": "not preserved"}]}),
        ]

    def test_summary_and_preserved_messages(self):
        path = self.write_jsonl(self.records())
        summary, kept, boundary = idle_eval.read_compaction(path)

        self.assertTrue(summary.startswith(idle_eval.MOD_PREFIX))
        self.assertIn("Use the blue door.", summary)
        self.assertEqual([r["uuid"] for r in kept], ["keep-user", "keep-assistant", "keep-result"])
        self.assertEqual(boundary["uuid"], "boundary")

        rendered = idle_eval.render_compaction(summary, kept)
        self.assertIn("## Summary", rendered)
        self.assertIn("keep this request", rendered)
        self.assertIn("keep this answer", rendered)
        self.assertIn('[tool Read: {"file_path": "/tmp/example"}]', rendered)
        self.assertIn("R" * 500, rendered)
        self.assertNotIn("R" * 501, rendered)
        self.assertNotIn("old request", rendered)
        self.assertNotIn("not preserved", rendered)


class TestModeCheck(unittest.TestCase):
    def test_mod_and_engine_framing(self):
        mod = idle_eval.MOD_PREFIX + " covers the earlier portion."
        engine = idle_eval.ENGINE_PREFIX + ". The summary below covers the earlier portion."

        self.assertEqual(idle_eval.summary_mode(mod), "mod")
        self.assertFalse(idle_eval.is_mode_fallback(mod))
        self.assertEqual(idle_eval.summary_mode(engine), "engine")
        self.assertTrue(idle_eval.is_mode_fallback(engine))
        self.assertEqual(idle_eval.summary_mode("something else"), "unknown")


class TestOutDirGuard(TranscriptCase):
    def test_refuses_a_directory_inside_the_work_tree(self):
        root = self.tmp / "repo"
        root.mkdir()

        with self.assertRaises(idle_eval.EvalError):
            idle_eval.ensure_outside_repo(root, root=root)
        with self.assertRaises(idle_eval.EvalError):
            idle_eval.ensure_outside_repo(root / "results", root=root)

        outside = self.tmp / "results"
        self.assertEqual(idle_eval.ensure_outside_repo(outside, root=root), outside.resolve())


class TestPassRule(unittest.TestCase):
    @staticmethod
    def entry(status, engine, mod):
        return {
            "status": status,
            "scores": {"engine": {"total": engine}, "mod": {"total": mod}},
        }

    def test_every_scored_transcript_must_reach_the_engine_total(self):
        self.assertTrue(idle_eval.overall_pass([
            self.entry("scored", 10, 10),
            self.entry("scored", 8, 12),
            self.entry("mode_fallback", 0, 0),
        ]))
        self.assertFalse(idle_eval.overall_pass([
            self.entry("scored", 10, 11),
            self.entry("scored", 12, 11),
        ]))
        self.assertFalse(idle_eval.overall_pass([
            self.entry("error", 0, 0),
            self.entry("scored", 5, 4),
        ]))


if __name__ == "__main__":
    unittest.main()
