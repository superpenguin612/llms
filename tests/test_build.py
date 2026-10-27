import os
import sqlite3
import subprocess
import time
from unittest import mock

import yaml

from kb import build
from tests.fixtures_kb import quiet_main
from kb.vault import open_vault
from tests.fixtures_kb import cd, sample_vault
from tests.helpers import VaultTestCase, make_vault


class TestBuild(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.root = sample_vault(self.tmp / "v", overrides={"contexts": [{"id": "ctx1"}]})
        self.v = open_vault(start=self.root)

    def db(self):
        return sqlite3.connect(self.root / ".kb/kb.sqlite")

    def test_index_views_and_agents(self):
        build.build(self.v)
        con = self.db()
        self.assertEqual(con.execute("SELECT origin, role FROM records WHERE id='fox-page'").fetchone(), ("external", "input"))
        self.assertIn(("fox-page", "tags", "animals"), con.execute("SELECT * FROM props").fetchall())
        self.assertEqual(con.execute("SELECT dst, anchor, kind, resolved FROM links WHERE src='foxes' AND kind='body'")
                         .fetchall(), [("fox-page", "e1", "body", 1)])
        self.assertEqual(con.execute("SELECT eid, location FROM excerpts").fetchall(), [("e1", "p. 1")])
        kinds = {r[0] for r in con.execute("SELECT kind FROM fts WHERE fts MATCH 'fox'")}
        self.assertEqual(kinds, {"body", "excerpt", "text"})
        base = yaml.safe_load((self.root / "views/evidence.base").read_text())
        self.assertEqual(base["filters"]["and"][0], 'note.type == "evidence"')
        self.assertEqual([x["type"] for x in base["views"]], ["table", "cards", "cards"])
        self.assertEqual(base["views"][1]["image"], "note.file")
        self.assertEqual(base["views"][2]["filters"], {"and": ['file.inFolder("evidence/ctx1")']})
        cat = (self.root / "views/source.md").read_text()
        self.assertIn("type: generated", cat)
        self.assertIn("[[fox-page]]", cat)
        agents = (self.root / "AGENTS.md").read_text()
        self.assertIn("| source | sources/ | 1 |", agents)
        self.assertEqual([r.id for r in open_vault(start=self.root).records], ["fox-page", "foxes"])  # views not records

    def test_rebuild_keeps_watch_tables_and_meta(self):
        build.build(self.v)
        con = self.db()
        con.execute("INSERT INTO watch_state VALUES ('git:x', 'fox-page', '{}', 'now')")
        con.execute("INSERT INTO meta VALUES ('last_digest', '2026-10-01')")
        con.commit()
        con.close()
        build.build(self.v)
        con = self.db()
        self.assertEqual(con.execute("SELECT count(*) FROM watch_state").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='last_digest'").fetchone()[0], "2026-10-01")

    def test_ensure_index_rebuilds_when_stale(self):
        build.ensure_index(self.v)
        t = build.built_at(self.v)
        build.ensure_index(self.v)
        self.assertEqual(build.built_at(self.v), t)
        note = self.root / "topics/foxes.md"
        future = time.time() + 5
        os.utime(note, (future, future))
        build.ensure_index(self.v)
        self.assertGreater(build.built_at(self.v), t)

    def test_repo_docs(self):
        repo = self.tmp / "repo"
        (repo / "docs").mkdir(parents=True)
        (repo / "docs/a.md").write_text("zebra crossing guide\n")
        (repo / "README.md").write_text("hello\n")
        (self.tmp / "registry.yaml").write_text(yaml.safe_dump({"vaults": {}, "repos": {
            "tool": {"path": str(repo), "docs": ["docs/**/*.md"], "vault": "example"},
            "other": {"path": str(repo), "vault": "elsewhere"}}}))
        ok = subprocess.CompletedProcess([], 0, stdout="abc123\n")
        with mock.patch("kb.build.subprocess.run", return_value=ok):
            build.build_index(self.v)
        refs = [r[0] for r in self.db().execute("SELECT ref FROM fts WHERE kind='repo-doc'")]
        self.assertEqual(refs, ["tool:docs/a.md@abc123"])
        warnings = []
        with mock.patch("kb.build.subprocess.run", side_effect=subprocess.CalledProcessError(128, "git")):
            build.build_index(self.v, warnings.append)
        self.assertEqual(len(warnings), 1)

    def test_cli(self):
        cd(self, self.root)
        self.assertEqual(quiet_main("build")[0], 0)
        log = self.root / "log.md"
        self.assertNotIn("| build |", log.read_text() if log.exists() else "")  # builds aren't logged (noise)


class TestGeneratedBlocks(VaultTestCase):
    def test_evidence_table_readme_nav_and_views(self):
        from kb.records import read_record
        from kb.vault import open_vault
        root = make_vault(self.tmp / "v", records={
            "outputs/finding/f1.md": ({"type": "output", "title": "F1", "kind": "finding"}, "\n# F1\n"),
            "evidence/lab/s1.md": ({"type": "evidence", "title": "S1", "description": "shows the thing",
                                    "kind": "screenshot", "context": "lab", "phase": "exploit",
                                    "contributor": ["alice"], "supports": ["[[f1]]"]}, "\n# S1\n")},
            files={"README.md": "# v\n\n## Purpose\n<!-- kb:todo --> x\n"})
        warnings = []
        build.build(open_vault(start=root), warnings.append)
        body = read_record(root / "outputs/finding/f1.md").body
        self.assertIn("| [[s1]] | exploit | shows the thing | alice |", body)
        readme = (root / "README.md").read_text()
        self.assertIn("<!-- kb:nav start -->", readme)
        self.assertIn("[[f1]]", readme)
        self.assertTrue(any("kb:todo" in w for w in warnings))
        base = (root / "views/evidence.base").read_text()
        self.assertNotIn("&id", base)
        self.assertIn("note.phase", base)
        self.assertIn("note.supports", base)


class TestObsidianTypes(VaultTestCase):
    def test_types_json_written_without_overriding_user_choices(self):
        import json
        from kb.vault import open_vault
        root = make_vault(self.tmp / "v", files={".obsidian/types.json": '{"types": {"updated": "text"}}'})
        build.build(open_vault(start=root))
        types = json.loads((root / ".obsidian/types.json").read_text())["types"]
        self.assertEqual(types["updated"], "text")       # user's choice kept
        self.assertEqual(types["tags"], "tags")
        self.assertEqual(types["supports"], "multitext")
        self.assertEqual(types["issued"], "text")         # partial dates allowed
