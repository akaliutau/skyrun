import copy

import pytest

from skyrun.config import Config, _normalize


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return root


@pytest.fixture
def config_factory(project):
    def make(raw=None):
        return Config(project, _normalize(copy.deepcopy(raw or {})))

    return make
