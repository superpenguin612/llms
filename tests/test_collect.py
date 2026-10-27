import contextlib
import io
import json
import os
from unittest import mock, skipUnless

import yaml

from kb import cli, collect
from kb.util import today
from kb.vault import open_vault
from tests.helpers import VaultTestCase, make_vault
from tests.test_capture import FakeResp, article, web
from tests.test_extract import HAS

GH = "https://api.github.com"


def j(data, url="x", headers=None):
    return FakeResp(json.dumps(data), url, ctype="application/json", headers=headers)


class TestCollect(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.entity_text = None
        self.root = make_vault(self.tmp / "kb", records={"entities/team/example-team.md": (
            {"type": "entity", "title": "Example Team", "kind": "team",
             "handles": ["github:example-org", "domain:example.com", "site:https://example.com/about"]},
            "\n# Example Team\n")})
        self.note = self.root / "entities/team/example-team.md"
        self.entity_text = self.note.read_text()

    def run_cli(self, *argv):
        out, cwd = io.StringIO(), os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(["collect", *argv])
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def test_github_cache_and_new_items(self):
        repo = {"full_name": "example-org/tool", "html_url": "https://github.com/example-org/tool",
                "pushed_at": "2026-10-01T00:00:00Z", "stargazers_count": 3, "forks_count": 1, "language": "Go",
                "topics": ["ctf"]}
        page2 = f"{GH}/organizations/1/repos?page=2"
        routes = {f"{GH}/users/example-org": j({"login": "example-org", "type": "Organization"}),
                  f"{GH}/orgs/example-org/repos?per_page=100&sort=pushed":
                      j([repo], headers={"Link": f'<{page2}>; rel="next"'}),
                  page2: j([{**repo, "full_name": "example-org/second"}])}
        with web(routes), mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            code, out = self.run_cli("example-team", "--collector", "github")
        self.assertEqual(code, 0)
        self.assertIn("github: 3 items, 3 new (first run)", out)
        cache = self.root / ".kb/collect/example-team/github" / f"{today()}.json"
        items = json.loads(cache.read_text())["items"]
        self.assertEqual(items[1]["stars"], 3)
        self.assertEqual(items[1]["topics"], ["ctf"])
        # a previous dated cache that already had one repo -> only the others are new
        prev = {"items": [{"key": "repo:example-org/tool"}, {"key": "account:example-org"}]}
        (cache.parent / "2026-01-01.json").write_text(json.dumps(prev))
        with web(routes):
            code, out = self.run_cli("example-team", "--collector", "github", "--json")
        [res] = json.loads(out)
        self.assertEqual([i["key"] for i in res["new"]], ["repo:example-org/second"])
        self.assertEqual(self.note.read_text(), self.entity_text)

    def test_github_project_releases(self):
        self.note.write_text(self.entity_text.replace("github:example-org", "github:example-org/tool"))
        routes = {f"{GH}/repos/example-org/tool": j({"full_name": "example-org/tool"}),
                  f"{GH}/repos/example-org/tool/releases?per_page=100":
                      j([{"tag_name": "v1.0", "published_at": "2026-09-01T00:00:00Z", "html_url": "u"}])}
        with web(routes):
            items = collect.collect_github(open_vault(start=self.root), "example-org/tool")
        self.assertEqual([i["key"] for i in items], ["release:example-org/tool@v1.0", "repo:example-org/tool"])

    def test_crtsh_unique_names(self):
        rows = [{"name_value": "a.example.com\nwww.example.com", "not_before": "2025-05-01T00:00:00"},
                {"name_value": "A.example.com", "not_before": "2024-01-01T00:00:00"}]
        with web({"https://crt.sh/?q=%25.example.com&output=json": j(rows)}):
            code, out = self.run_cli("example-team", "--collector", "crtsh", "--json")
        [res] = json.loads(out)
        names = {i["name"]: i["date"] for i in res["new"]}
        self.assertEqual(names, {"a.example.com": "2024-01-01T00:00:00", "www.example.com": "2025-05-01T00:00:00"})

    @skipUnless(HAS["trafilatura"], "trafilatura not installed")
    def test_site_captures_linked_source(self):
        url = "https://example.com/about"
        with web({url: FakeResp(article(), url)}):
            code, out = self.run_cli("example-team", "--collector", "site")
        self.assertEqual(code, 0)
        v = open_vault(start=self.root)
        [src] = v.of_type("source")
        self.assertEqual(src.fm["entities"], ["[[example-team]]"])
        m = yaml.safe_load((self.root / src.fm["original"]).parent.joinpath("capture.yaml").read_text())
        self.assertEqual(m["obtained_from"], "kb collect example-team")
        self.assertEqual(self.note.read_text(), self.entity_text)

    def test_errors_reported(self):
        with web({f"{GH}/users/example-org": 403}):
            code, out = self.run_cli("example-team", "--collector", "github")
        self.assertEqual(code, 1)
        self.assertIn("rate limit", out)
