import copy

import pytest

from skyrun.config import _normalize, discover, load_config
from skyrun.errors import ConfigError


def test_defaults_are_applied_without_mutating_configuration():
    raw = {
        "run": {"app_name": "my-project", "gpu": "cpu"},
        "modal": {"artifact_volume": "custom-artifacts"},
    }
    original = copy.deepcopy(raw)
    config = _normalize(raw)
    assert config["run"]["gpu"] == "cpu"
    assert config["modal"]["artifact_volume"] == "custom-artifacts"
    assert config["modal"]["cache_volume"] == "my-project-cache"
    assert config["modal"]["source_volume"] == "my-project-source"
    assert config["image"]["requirements"] is None
    assert raw == original


def test_discovery_and_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="ordinary"\n[tool.skyrun.run]\ngpu="cpu"\n'
    )
    child = tmp_path / "nested"
    child.mkdir()
    assert discover(child) == tmp_path / "pyproject.toml"
    assert load_config(start=child)["run"]["gpu"] == "cpu"
    (tmp_path / "skyrun.toml").write_text('[run]\ngpu="L4"\n')
    assert load_config(start=child)["run"]["gpu"] == "L4"


def test_overrides_are_independent(config_factory):
    original = config_factory()
    changed = original.override(gpu="cpu", timeout=30, command=["python", "other.py"])
    assert changed["run"]["gpu"] == "cpu"
    assert original["run"]["gpu"] == "T4"
    assert changed["workload"]["command"] == ["python", "other.py"]


@pytest.mark.parametrize(
    "raw",
    [
        {"unknown": True},
        {"sync": {"typo": 1}},
        {"run": {"cpu": True}},
        {"run": {"cpu": float("nan")}},
        {"run": {"cpu": 0}},
        {"run": {"memory_mib": -1}},
        {"run": {"timeout_seconds": True}},
        {"run": {"timeout_seconds": 86400}},
        {"run": {"startup_timeout_seconds": 0}},
        {"run": {"shutdown_grace_seconds": 3601}},
        {"sync": {"max_upload_files": False}},
        {"sync": {"max_upload_mib": 0}},
        {"sync": {"project_dir": "../other"}},
        {"sync": {"project_dir": ""}},
        {"sync": {"local_dir": "."}},
        {"sync": {"ignore_file": "../ignore"}},
        {"sync": {"output_dirs": ["outputs", "outputs/nested"]}},
        {"sync": {"output_dirs": ["_sync"]}},
        {"sync": {"output_dirs": ["_runner"]}},
        {"sync": {"bidirectional": ["data", "data/config.json"]}},
        {"sync": {"bidirectional": ["data/*.json"]}},
        {"sync": {"bidirectional": [".git"]}},
        {"sync": {"bidirectional": [".skyrun.lock"]}},
        {"sync": {"bidirectional": [".SKYRUN.LOCK"]}},
        {"sync": {"bidirectional": ["nested/.skyrun.lock"]}},
        {"sync": {"bidirectional": ["outputs"]}},
        {"environment": {"BAD-NAME": "x"}},
        {"environment": {"OK": 1}},
        {"environment": {"OK": "a\x00b"}},
        {"workload": {"terminal": "true"}},
        {"workload": {"command": []}},
        {"workload": {"command": ["python", 1]}},
        {"workload": {"command": ["a\x00b"]}},
        {"image": {"apt_packages": "git"}},
        {"image": {"python_version": "3.10"}},
        {"image": {"requirements": "../private"}},
        {"modal": {"cache_volume": "same", "artifact_volume": "same"}},
        {"modal": {"secrets": "secret"}},
        {"modal": {"gpu": "cpu"}},
        {"modal": {"cpu": 2}},
        {"modal": []},
        {"run": []},
        {"provider": "Bad Provider"},
        {"modal": {"gpu": "L4"}, "run": {"gpu": "T4"}},
    ],
)
def test_invalid_config_fails_before_provider_contact(raw):
    with pytest.raises(ConfigError):
        _normalize(raw)


def test_explicit_absolute_project(config_factory, tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    assert config_factory().override(project_dir=folder).project == folder


def test_invalid_toml_has_actionable_error(tmp_path):
    path = tmp_path / "skyrun.toml"
    path.write_text("[run\ngpu =\n")
    with pytest.raises(ConfigError, match="cannot load configuration"):
        load_config(path)
