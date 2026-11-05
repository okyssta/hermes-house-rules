"""Shared harness: a throwaway HERMES_HOME, a throwaway git project, no plugin host.

The rules are exercised the way they run in production — real files on disk, the
real hook handler, the real regex engine — but against temp directories, so a test
can never touch the developer's own policy or the repository it lives in.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/root/.hermes/hermes-agent"))


def pytest_configure(config):
    # The plugin is a package directory; its *parent* must be importable for
    # `import house_rules`, exactly like an installed plugin.
    for path in (str(REPO_ROOT.parent), str(HERMES_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


class Env:
    """The two directories a test needs: a Hermes home and a git project."""

    def __init__(self, home: Path, project: Path):
        self.home = home
        self.project = project

    @property
    def project_rules(self) -> Path:
        return self.project / ".hermes" / "rules.json"

    @property
    def global_rules(self) -> Path:
        return self.home / "plugin-data" / "house_rules" / "rules.json"

    @property
    def hits(self) -> Path:
        return self.home / "plugin-data" / "house_rules" / "hits.json"

    def write(self, path: Path, payload) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(project)

    import house_rules
    from house_rules import matching

    monkeypatch.setattr(house_rules, "_CTX", None)
    house_rules.reset_settings_cache()
    matching.clear_cache()
    yield Env(home, project)
    house_rules.reset_settings_cache()
    matching.clear_cache()


@pytest.fixture()
def settings():
    from house_rules.settings import Settings

    return Settings()
