import contextlib
import io

from unittest import mock

import yaml

from kb import cli
from kb.vault import load_registry, registry_path
from tests.helpers import VaultTestCase, make_vault


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class TestRegistry(VaultTestCase):
    def test_add_list_repo_remove(self):
        shared = make_vault(self.tmp / "a")
        local = make_vault(self.tmp / "b", overrides={"access": "local"})
        registry_path().write_text(yaml.safe_dump({"vaults": {}, "repos": {}, "custom": {"keep": 1}}))
        self.assertEqual(run("registry", "add", "a", str(shared))[0], 0)
        self.assertEqual(run("registry", "add", "b", str(local))[0], 0)
        code, msg = run("registry", "add", "b2", str(local), "--access", "shared")
        self.assertEqual(code, 2)
        self.assertIn("access: local", msg)
        self.assertEqual(run("registry", "add-repo", "tool", str(self.tmp), "--vault", "a", "--owner", "club")[0], 0)
        self.assertEqual(run("registry", "add-repo", "tool2", str(self.tmp), "--vault", "nope")[0], 2)
        reg = load_registry()
        self.assertEqual(reg["custom"], {"keep": 1})
        self.assertEqual(reg["vaults"]["b"]["access"], "local")
        self.assertEqual(reg["repos"]["tool"], {"path": str(self.tmp), "vault": "a",
                                               "docs": ["README.md", "docs/**/*.md"], "owner": "club"})
        code, out = run("registry", "list")
        self.assertIn("a · shared ·", out)
        self.assertIn("tool → a", out)
        self.assertEqual(run("registry", "remove", "tool")[0], 0)
        self.assertEqual(run("registry", "remove", "tool")[0], 2)
        self.assertNotIn("tool", load_registry()["repos"])

    def test_doctor(self):
        root = make_vault(self.tmp / "a", overrides={"name": "demo", "access": "local"})
        with contextlib.chdir(root):
            code, out = run("doctor")
        self.assertEqual(code, 0)
        self.assertIn("git", out)
        self.assertIn("vault: demo", out)
        self.assertIn("may not read it", out)

    def test_doctor_markitdown_extras(self):
        import importlib.util
        real = importlib.util.find_spec
        with mock.patch("importlib.util.find_spec", lambda m, *a: None if m == "mammoth" else real(m, *a)), \
                contextlib.chdir(self.tmp):
            code, out = run("doctor")
        self.assertRegex(out, r"--  markitdown docx  \(optional; pip install 'markitdown\[docx,xlsx,pptx\]'\)")
