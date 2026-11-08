import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

modal = pytest.importorskip("modal")

from skyrun.manifest import snapshot
from skyrun.remote import app
from skyrun.runner import make_spec


class Channel:
    def __init__(self):
        self.messages = defaultdict(list)

    def put(self, value, *, partition, **kwargs):
        self.messages[partition].append(value)

    def get(self, **kwargs):
        return "start"

    def get_many(self, *args, **kwargs):
        return []


def setup_worker(config_factory, project, tmp_path, monkeypatch):
    config = config_factory({"run": {"gpu": "cpu"}, "workload": {"terminal": False}})
    frozen = snapshot(config, tmp_path / "snapshot")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    for entry in frozen.manifest["files"]:
        (blobs / entry["sha256"]).write_bytes((frozen.project / entry["path"]).read_bytes())
    commits = []
    monkeypatch.setattr(
        modal.Volume,
        "from_name",
        lambda name, **kwargs: SimpleNamespace(commit=lambda: commits.append(name)),
    )
    monkeypatch.setattr(app, "SOURCE_BLOBS", blobs)
    monkeypatch.setattr(app, "ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(app, "CACHE_ROOT", tmp_path / "cache")
    return config, frozen, commits


def test_worker_handshake_logs_and_commits(config_factory, project, tmp_path, monkeypatch):
    config, frozen, commits = setup_worker(config_factory, project, tmp_path, monkeypatch)
    spec = make_spec(config, frozen, "test-run")
    channel = Channel()
    result = app.execute(spec, channel)
    assert result["returncode"] == 0
    assert channel.messages["events"] == ["ready"]
    assert b"hello" in b"".join(channel.messages["output"])
    assert commits == ["skyrun-artifacts", "skyrun-cache"]
    assert (tmp_path / "artifacts/test-run/_runner/console.log").read_bytes() == b"hello\n"


def test_source_setup_failure_is_recoverable(config_factory, project, tmp_path, monkeypatch):
    config, frozen, commits = setup_worker(config_factory, project, tmp_path, monkeypatch)
    spec = make_spec(config, frozen, "test-run")
    for blob in (tmp_path / "blobs").iterdir():
        blob.write_text("corrupt")
    result = app.execute(spec, Channel())
    assert result["returncode"] == 1
    assert not result["started"]
    assert "integrity" in result["error"]
    assert len(commits) == 2
    record = tmp_path / "artifacts/test-run/_runner/result.json"
    assert json.loads(record.read_text())["status"] == "failed"


def test_modal_symlink_mountpoints_are_resolved_at_the_provider_boundary(
    config_factory, project, tmp_path, monkeypatch
):
    config, frozen, commits = setup_worker(config_factory, project, tmp_path, monkeypatch)
    mounts = tmp_path / "mounts"
    mounts.mkdir()
    artifacts = tmp_path / "actual-artifacts"
    artifacts.mkdir()
    (mounts / "artifacts").symlink_to(artifacts, target_is_directory=True)
    (mounts / "source").symlink_to(tmp_path, target_is_directory=True)
    (mounts / "cache").symlink_to(tmp_path / "actual-cache", target_is_directory=True)
    monkeypatch.setattr(app, "ARTIFACT_ROOT", mounts / "artifacts/runs")
    monkeypatch.setattr(app, "SOURCE_BLOBS", mounts / "source/blobs")
    monkeypatch.setattr(app, "CACHE_ROOT", mounts / "cache")
    result = app.execute(make_spec(config, frozen, "test-run"), Channel())
    assert result["returncode"] == 0
    assert (artifacts / "runs/test-run/_runner/console.log").read_bytes() == b"hello\n"
    assert len(commits) == 2


def test_symlinks_inside_a_volume_are_still_rejected(
    config_factory, project, tmp_path, monkeypatch
):
    config, frozen, commits = setup_worker(config_factory, project, tmp_path, monkeypatch)
    artifacts, outside = tmp_path / "artifacts", tmp_path / "outside"
    artifacts.mkdir()
    outside.mkdir()
    (artifacts / "runs").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(app, "ARTIFACT_ROOT", artifacts / "runs")
    with pytest.raises(ValueError, match="symlink"):
        app.execute(make_spec(config, frozen, "test-run"), Channel())
    assert list(outside.iterdir()) == []
