"""Test helpers: build throwaway vaults from a starter profile. No network."""

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from kb.profile import starter_text
from kb.records import dump_front_matter


def make_vault(root, profile="generic", overrides=None, records=None, files=None) -> Path:
    """Create a vault at `root`.
    overrides: dict merged into the profile's top level (e.g. {"access": "local"}).
    records: {relpath: (front_matter_dict, body_str)}; files: {relpath: bytes | str}."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(starter_text(profile))
    data.update(overrides or {})
    (root / "kb.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    for rel, (fm, body) in (records or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump_front_matter(fm) + body, encoding="utf-8")
    for rel, data_ in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        (p.write_bytes if isinstance(data_, bytes) else p.write_text)(data_)
    return root


class VaultTestCase(unittest.TestCase):
    """Gives each test a temp dir (self.tmp), an isolated registry, and no KB_AGENT."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(os.chdir, os.getcwd())  # tests that chdir can't break later tests
        self._env = {k: os.environ.get(k) for k in ("KB_REGISTRY", "KB_AGENT")}
        os.environ["KB_REGISTRY"] = str(self.tmp / "registry.yaml")
        os.environ.pop("KB_AGENT", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()
