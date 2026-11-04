"""contrib/git-hooks/dispatch: repo policy from a made-up repos.yaml, in a throwaway repo."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "contrib" / "git-hooks"


@unittest.skipUnless(shutil.which("git") and shutil.which("sh"), "needs git and sh")
class TestPreCommit(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.home = self.tmp / "home" / "someone"
        self.policy = self.home / "src" / "agents" / "agent-vault" / "global" / "repos.yaml"
        self.repo = self.tmp / "demo"
        (self.repo / "src").mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home))
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES"):
            self.env.pop(key, None)
        self.git("init", "-q")
        # A branch named main without needing a commit or a configured identity.
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("remote", "add", "origin", "https://github.com/example/demo.git")
        (self.repo / "src" / "demo.txt").write_text("made-up content\n")
        self.git("add", "src/demo.txt")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, check=True)

    def policy_says(self, *lines):
        self.policy.parent.mkdir(parents=True, exist_ok=True)
        self.policy.write_text("default: main\n" + "".join(line + "\n" for line in lines))

    def pre_commit(self):
        r = subprocess.run([str(HOOKS / "pre-commit")], cwd=self.repo, env=self.env,
                           capture_output=True, text=True)
        return r.returncode, r.stderr

    def test_third_field_is_ignored_for_main(self):
        self.policy_says("example/demo: main opus")
        code, err = self.pre_commit()
        self.assertEqual(code, 0, err)

    def test_pr_mode_blocks_commit_to_main(self):
        self.policy_says("example/demo: pr")
        code, err = self.pre_commit()
        self.assertNotEqual(code, 0)
        self.assertIn("don't commit to main", err)

    def test_pr_mode_with_third_field_blocks_commit_to_main(self):
        self.policy_says("example/demo: pr opus")
        code, err = self.pre_commit()
        self.assertNotEqual(code, 0)
        self.assertIn("don't commit to main", err)


if __name__ == "__main__":
    unittest.main()
