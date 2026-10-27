import json

import yaml

from tests.fixtures_kb import quiet_main
from kb.util import KBError
from kb.init import init
from tests.helpers import VaultTestCase


class TestInit(VaultTestCase):
    def test_init_writes_vault(self):
        root = self.tmp / "proj" / "knowledge-base"
        v = init(root, "competition")
        self.assertEqual(v.name, "proj")
        self.assertEqual(yaml.safe_load((root / "kb.yaml").read_text())["name"], "proj")
        for f in ("AGENTS.md", "README.md", "log.md", ".obsidian/app.json", ".obsidian/core-plugins.json",
                  "templates/source.md", "templates/evidence.md"):
            self.assertTrue((root / f).is_file(), f)
        self.assertTrue((root / "entities").is_dir() and (root / "evidence").is_dir())
        self.assertIn(".kb/", (root / ".gitignore").read_text())
        self.assertIn("<!-- kb:agents start -->", (root / "AGENTS.md").read_text())
        self.assertTrue(json.loads((root / ".obsidian/core-plugins.json").read_text())["bases"])
        self.assertFalse(list(root.rglob("CLAUDE.md")))
        tmpl = (root / "templates/source.md").read_text()
        self.assertNotIn("''", tmpl)  # no empty placeholder values
        self.assertIn("## Excerpts", tmpl)
        self.assertIn("<!-- kb:todo -->", (root / "README.md").read_text())
        self.assertIn("kb guide", (root / "AGENTS.md").read_text())
        self.assertNotIn("tier:", (root / "templates/note.md").read_text())
        with self.assertRaises(KBError):
            init(root, "generic")

    def test_cli(self):
        self.assertEqual(quiet_main("init", str(self.tmp / "v"), "--profile", "agent-vault", "--name", "av")[0], 0)
        self.assertEqual(yaml.safe_load((self.tmp / "v/kb.yaml").read_text())["name"], "av")
        self.assertEqual(quiet_main("init", str(self.tmp / "v"))[0], 2)
        self.assertEqual(quiet_main("init", str(self.tmp / "w"), "--profile", "nope")[0], 2)
