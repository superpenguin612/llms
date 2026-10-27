import os
import time

import yaml

from tests.fixtures_kb import quiet_main
from kb.lint import Linter
from kb.vault import open_vault
from tests.fixtures_kb import cd, sample_vault
from tests.helpers import VaultTestCase, make_vault


def rules(root, **kw):
    return {(f.severity, f.rule) for f in Linter(open_vault(start=root), **kw).run()}


class TestLint(VaultTestCase):
    def test_clean_vault(self):
        root = sample_vault(self.tmp / "v")
        found = rules(root)
        self.assertFalse({f for f in found if f[0] == "error"}, found)

    def test_errors(self):
        root = sample_vault(self.tmp / "v", records={
            "topics/bad.md": ({"type": "topic", "title": "Bad", "tier": "primary", "status": "wip", "id": "other",
                              "aliases": ["fox-page"]},
                             "\n[[missing]] [[fox-page#e9]] [[foxes#Nope]] [[foxes#Detail]]\n"),
            "topics/nokind.md": ({"type": "widget", "title": "W"}, ""),
            "sources/src3.md": ({"type": "source", "title": "S3", "tier": "primary", "originals_note": "n/a"}, ""),
            "sources/src2.md": ({"type": "source", "title": "S2", "kind": "pamphlet", "originals": []},
                                "\n## Excerpts\n### e1\n> not in the text at all\n### e2\n> The quick brown, fox\n"),
        }, files={"topics/broken.md": "---\ntype: [unclosed\n---\n", "text/src2.md": "The quick brown fox.\n", "sub/CLAUDE.md": "x",
                  "intake/2026-10-01-r1.tsv": "file\tsha256\tdisposition\ttarget\na.png\tab\t\t\nb.png\tcd\tevidence\tx\n",
                  "originals/fox-page/2026-10-01/page.html": b"tampered"})
        found = rules(root, fixity=True)
        for rule in ("type", "id", "required", "value", "tier", "link", "anchor", "excerpt", "originals",
                     "fixity", "claude-md", "intake", "duplicate-alias"):
            self.assertIn(("error", rule), found, rule)
        self.assertIn(("warning", "excerpt-loose"), found)
        v = open_vault(start=root)
        self.assertEqual(len(v.load_errors), 1)  # broken.md is reported by the yaml rule
        self.assertIn(("error", "yaml"), found)

    def test_scope_fields_checked(self):
        scope = {"season": "int", "event": "string", "region": ["east", "other"]}
        root = sample_vault(self.tmp / "v", overrides={"scope_fields": scope}, records={
            "topics/ok.md": ({"type": "topic", "title": "Ok", "season": 2026, "region": "east"}, "\n[[foxes]]\n"),
            "topics/bad.md": ({"type": "topic", "title": "Bad", "season": "x", "region": "zz"}, "\n[[foxes]]\n")})
        msgs = sorted((f.path, f.message) for f in Linter(open_vault(start=root)).run() if f.rule == "value")
        self.assertEqual([m[0] for m in msgs], ["topics/bad.md", "topics/bad.md"])
        self.assertTrue(any(m[1].startswith("region: 'zz' is not one of") for m in msgs), msgs)
        from kb.util import KBError
        with self.assertRaisesRegex(KBError, "scope_fields.season: use one of"):
            open_vault(start=sample_vault(self.tmp / "w", overrides={"scope_fields": {"season": "year"}}))

    def test_non_string_core_keys_flagged(self):
        root = sample_vault(self.tmp / "v", records={
            "topics/bad.md": ({"type": "topic", "title": "Bad", "description": {"Foo": "bar baz"}, "x_note": 3}, "\n[[foxes]]\n")})
        msgs = [f.message for f in Linter(open_vault(start=root)).run() if f.path == "topics/bad.md" and f.rule == "value"]
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(all("quote the value" in m for m in msgs))

    def test_writer_quotes_colon_space(self):
        from kb.records import dump_yaml
        self.assertEqual(yaml.safe_load(dump_yaml({"description": "Foo: bar baz"})), {"description": "Foo: bar baz"})

    def test_h1_must_match_title(self):
        root = sample_vault(self.tmp / "v", records={
            "topics/same.md": ({"type": "topic", "title": "Same"}, "\n```\n# code\n```\n# Same\n[[foxes]]\n"),
            "topics/drift.md": ({"type": "topic", "title": "New title"}, "\n# drift\n[[foxes]]\n")})
        bad = [f.path for f in Linter(open_vault(start=root)).run() if f.rule == "h1"]
        self.assertEqual(bad, ["topics/drift.md"])

    def test_warnings(self):
        old = time.time() - 10 * 86400
        root = sample_vault(self.tmp / "v", overrides={"caps": {"warn_mb": 0.000001}}, records={
            "topics/lonely.md": ({"type": "topic", "title": "L", "description": "x" * 201}, "\nno links\n"),
            "sources/stale.md": ({"type": "source", "title": "S", "kind": "webpage", "tier": "primary",
                                  "refresh": "weekly", "last_checked": "2020-01-01"}, "\n[[foxes]]\n"),
        }, files={"inbox/david/old.png": b"x"})
        os.utime(root / "inbox/david/old.png", (old, old))
        found = rules(root)
        for rule in ("description", "uncited", "tracked-due", "inbox-age", "large-file"):
            self.assertIn(("warning", rule), found, rule)

    def test_tags_and_local_links(self):
        local = sample_vault(self.tmp / "private", overrides={"access": "local", "name": "priv"})
        (self.tmp / "registry.yaml").write_text(yaml.safe_dump({"vaults": {"priv": {"path": str(local)}}}))
        root = sample_vault(self.tmp / "v", overrides={"tags": ["plants"]}, records={
            "topics/leak.md": ({"type": "topic", "title": "Leak", "sources": ["[[fox-page]]"]},
                              f"\nsee {local}/notes/x.md\n")})
        found = rules(root)
        self.assertIn(("error", "tag"), found)
        self.assertIn(("error", "local-link"), found)

    def test_cli_strict_and_deep(self):
        root = sample_vault(self.tmp / "v")
        cd(self, root)
        self.assertEqual(quiet_main("lint", "--strict")[0], 1)   # no kb:agents block yet
        self.assertEqual(quiet_main("build")[0], 0)
        self.assertEqual(quiet_main("lint", "--strict", "--deep")[0], 0)
        self.assertFalse((root / "log.md").exists() and "lint" in (root / "log.md").read_text())  # lint never logs


def manifest(files, main=None):
    from kb.util import sha256_bytes
    return yaml.safe_dump({"captured": "2026-01-01T00:00:00Z", "via": "file", "main": main or next(iter(files), None),
                           "files": [{"name": n, "sha256": sha256_bytes(b), "bytes": len(b), "format": "txt"}
                                     for n, b in files.items()]})


def found(root, **kw):
    return {(f.severity, f.rule, f.path) for f in Linter(open_vault(start=root), **kw).run()}


class TestOriginalsTree(VaultTestCase):
    def a_record(self, **fm):
        from kb.util import sha256_bytes
        return {"sources/a.md": ({"type": "source", "title": "A", "kind": "pdf", "tier": "primary",
                                  "original": "originals/a/2026-01-01/a.txt", "sha256": sha256_bytes(b"one"),
                                  "captured": "2026-01-01T00:00:00Z", **fm}, "\n# A\n")}

    def test_orphan_and_modified_originals(self):
        import subprocess
        root = make_vault(self.tmp / "v", records=self.a_record(), files={
            "originals/a/2026-01-01/a.txt": "one", "originals/a/2026-01-01/capture.yaml": manifest({"a.txt": b"one"}),
            "originals/stray.bin": b"x", "originals/nobody/2026-01-01/capture.yaml": manifest({})})
        g = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-qm", "init"], check=True)
        (root / "originals/a/2026-01-01/a.txt").write_text("two")
        (root / "originals/a/2026-01-01/capture.yaml").write_text(manifest({"a.txt": b"two"}))
        rules = {(f.rule, f.path) for f in Linter(open_vault(start=root), fixity=True).run()}
        self.assertIn(("orphan-original", "originals/stray.bin"), rules)
        self.assertIn(("orphan-original", "originals/nobody"), rules)
        self.assertIn(("immutable", "originals/a/2026-01-01/a.txt"), rules)
        self.assertIn(("immutable", "originals/a/2026-01-01/capture.yaml"), rules)
        self.assertIn(("fixity", "sources/a.md"), rules)

    def test_manifest_rules(self):
        root = make_vault(self.tmp / "v", records=self.a_record(aliases=["a-alias"]), files={
            "originals/a/2026-01-01/a.txt": "one", "originals/a/2026-01-01/extra.txt": "x",
            "originals/a/2026-01-01/capture.yaml": manifest({"a.txt": b"one", "gone.txt": b"g"}),
            "originals/a/2026-01-02/b.txt": "changed",
            "originals/a/2026-01-02/capture.yaml": manifest({"b.txt": b"original bytes"}),
            "originals/a/2026-01-03/c.txt": "no manifest",
            "originals/a/2026-01-04/capture.yaml": "files: [unclosed",
            "originals/a-alias/2026-01-01/capture.yaml": manifest({})})
        f = found(root, fixity=True)
        self.assertIn(("error", "manifest", "originals/a/2026-01-01/extra.txt"), f)    # unlisted
        self.assertIn(("error", "manifest", "originals/a/2026-01-01/gone.txt"), f)     # listed, missing
        self.assertIn(("error", "fixity", "originals/a/2026-01-02/b.txt"), f)          # sha mismatch
        self.assertIn(("error", "manifest", "originals/a/2026-01-03"), f)              # no capture.yaml
        self.assertIn(("error", "manifest", "originals/a/2026-01-04"), f)              # bad YAML
        self.assertFalse([x for x in f if x[2].startswith("originals/a-alias")])       # alias owns its folder
        self.assertNotIn(("error", "fixity", "sources/a.md"), f)

    def test_old_format_is_one_clear_error(self):
        root = make_vault(self.tmp / "v", records={"sources/a.md": (
            {"type": "source", "title": "A", "kind": "pdf", "tier": "primary",
             "originals": [{"path": "originals/a/2026-01-01/a.txt", "sha256": "x"}]}, "\n# A\n")},
            files={"originals/a/2026-01-01/a.txt": "one"})
        fs = [x for x in Linter(open_vault(start=root)).run() if x.severity == "error"]
        self.assertEqual([(x.rule, x.path) for x in fs], [("originals", "sources/a.md")])
        self.assertIn("kb migrate originals", fs[0].message)

    def test_missing_original_and_note(self):
        root = make_vault(self.tmp / "v", records={
            "sources/n.md": ({"type": "source", "title": "N", "kind": "pdf", "tier": "primary",
                              "original_note": "paywalled; read in a browser"}, "\n"),
            "sources/m.md": ({"type": "source", "title": "M", "kind": "pdf", "tier": "primary",
                              "original": "originals/m/2026-01-01/m.pdf", "sha256": "x"}, "\n")})
        f = found(root)
        self.assertNotIn(("error", "originals", "sources/n.md"), f)
        self.assertIn(("error", "originals", "sources/m.md"), f)

    def test_clone_root_ignored(self):
        root = make_vault(self.tmp / "v", overrides={"clone_root": "~/somewhere"})
        self.assertIn(("warning", "clone-root", "kb.yaml"), found(root))


class TestRepoExcerpts(VaultTestCase):
    def setUp(self):
        super().setUp()
        import subprocess
        self.repo = repo = self.tmp / "clones/github.com/example/tool"
        repo.mkdir(parents=True)
        g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "README.md").write_text("# Tool\nIt does one careful thing.\n")
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-qm", "init"], check=True)
        self.sha = subprocess.run(g + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def vault(self, text):
        body = ("\n## Excerpts\n### e1\n> It does one careful thing.\nLocation: README.md L2 @ " + self.sha[:7] +
                "\n\n### e2\n> Not in the file.\nLocation: README.md L3\n")
        return make_vault(self.tmp / "v", records={"sources/tool.md": (
            {"type": "source", "title": "Tool", "kind": "repo", "tier": "primary", "url": "https://github.com/example/tool",
             "original_note": "test", "text": text}, body)})

    def test_portable_clone_resolves_against_kb_clones(self):
        f = found(self.vault(f"clone:github.com/example/tool@{self.sha}"))
        self.assertNotIn(("error", "excerpt", "sources/tool.md e1"), f)
        self.assertIn(("error", "excerpt", "sources/tool.md e2"), f)
        self.assertFalse([x for x in f if x[1].startswith("clone")])

    def test_old_clone_path_warns(self):
        f = found(self.vault(f"clone:{self.repo}@{self.sha}"))
        self.assertIn(("warning", "clone-path", "sources/tool.md"), f)
        self.assertNotIn(("error", "excerpt", "sources/tool.md e1"), f)

    def test_missing_clone_is_a_warning_and_skips_excerpts(self):
        os.environ["KB_CLONES"] = str(self.tmp / "empty")
        ls = Linter(open_vault(start=self.vault(f"clone:github.com/example/tool@{self.sha}"))).run()
        self.assertEqual([(x.severity, x.rule) for x in ls if x.path.startswith("sources/tool.md")],
                         [("warning", "clone-missing")])
        msg = [x.message for x in ls if x.rule == "clone-missing"][0]
        self.assertIn(str(self.tmp / "empty/github.com/example/tool"), msg)
        self.assertIn("kb capture https://github.com/example/tool", msg)
