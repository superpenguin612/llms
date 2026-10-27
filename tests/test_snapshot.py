import contextlib
import io
import os
import subprocess
from unittest import mock

import yaml

from kb import cli
from kb.vault import registry_path
from tests.helpers import VaultTestCase, make_vault
from tests.test_watch import GIT_ENV


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class TestSnapshot(VaultTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, GIT_ENV)
        p.start()
        self.addCleanup(p.stop)

    def repo_with_vault(self, name, **overrides):
        repo = self.tmp / name
        root = make_vault(repo / "knowledge-base", overrides={"name": name, **overrides})
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        return repo, root

    def test_tag_refuse_and_list(self):
        repo, root = self.repo_with_vault("one")
        with contextlib.chdir(repo):
            code, out = run("snapshot", "morning", "-m", "before the event")
            self.assertEqual(code, 0, out)
            self.assertIn("kb/morning at", out)
            self.assertEqual(git(repo, "cat-file", "-t", "kb/morning").strip(), "tag")
            code, out = run("snapshot", "morning")
            self.assertEqual(code, 2)
            self.assertIn("already exists", out)
            code, out = run("snapshot", "list")
        self.assertIn("kb/morning · ", out)
        self.assertIn("before the event", out)
        self.assertIn("snapshot | kb/morning", (root / "log.md").read_text())

    def test_all_skips_local(self):
        _, a = self.repo_with_vault("one")
        _, b = self.repo_with_vault("two", access="local")
        registry_path().write_text(yaml.safe_dump({"vaults": {"one": {"path": str(a)}, "two": {"path": str(b)},
                                                              "gone": {"path": str(self.tmp / "missing")}}}))
        code, out = run("snapshot", "s1", "--all")
        self.assertEqual(code, 0, out)
        self.assertIn("one: kb/s1", out)
        self.assertIn("two: skipped, local-only", out)
        self.assertIn("gone: skipped", out)

    def test_not_a_repo(self):
        root = make_vault(self.tmp / "plain")
        with contextlib.chdir(root):
            code, out = run("snapshot", "x")
        self.assertEqual(code, 2)
        self.assertIn("git init", out)
