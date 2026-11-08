import json
import shutil

import pytest

from skyrun.errors import SyncConflict
from skyrun.manifest import snapshot
from skyrun.runner import make_spec
from skyrun.sync import apply_sync, collect_sync


@pytest.fixture
def sync_setup(config_factory, project, tmp_path):
    data = project / "data"
    data.mkdir()
    (data / "config.json").write_text("original")
    config = config_factory({"sync": {"bidirectional": ["data"]}})
    frozen = snapshot(config, tmp_path / "snapshot")
    workspace = tmp_path / "remote"
    shutil.copytree(frozen.project, workspace)
    run_dir = tmp_path / "artifacts"
    spec = make_spec(config, frozen, "test-run")
    return config, workspace, run_dir, spec


def test_modified_and_new_files_round_trip(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").write_text("remote")
    (workspace / "data/new.json").write_text("new")
    (workspace / "main.py").write_text("unselected change")
    collect_sync(spec, workspace, run_dir)
    report = apply_sync(config.project, run_dir, selections=["data"])
    assert set(report["applied"]) == {"data/config.json", "data/new.json"}
    assert (config.project / "data/config.json").read_text() == "remote"
    assert (config.project / "data/new.json").read_text() == "new"
    assert (config.project / "main.py").read_text() == "print('hello')\n"
    assert apply_sync(config.project, run_dir)["applied"] == []


def test_unchanged_files_are_not_downloaded(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    collect_sync(spec, workspace, run_dir)
    manifest = json.loads((run_dir / "_sync/manifest.json").read_text())
    assert manifest["files"] == []
    assert apply_sync(config.project, run_dir)["applied"] == []


def test_conflict_preserves_all_local_files_and_remote_artifacts(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").write_text("remote")
    (workspace / "data/new.json").write_text("new")
    (config.project / "data/config.json").write_text("local edit")
    collect_sync(spec, workspace, run_dir)
    with pytest.raises(SyncConflict) as error:
        apply_sync(config.project, run_dir)
    assert error.value.paths == ("data/config.json",)
    assert (config.project / "data/config.json").read_text() == "local edit"
    assert not (config.project / "data/new.json").exists()
    assert (run_dir / "_sync/files/data/config.json").read_text() == "remote"
    assert json.loads((run_dir / "sync.json").read_text())["conflicts"] == ["data/config.json"]


@pytest.mark.parametrize("policy,expected", [("local", "local"), ("remote", "remote")])
def test_explicit_conflict_policies(sync_setup, policy, expected):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").write_text("remote")
    (config.project / "data/config.json").write_text("local")
    collect_sync(spec, workspace, run_dir)
    report = apply_sync(config.project, run_dir, conflict=policy)
    assert report["conflicts"] == ["data/config.json"]
    assert (config.project / "data/config.json").read_text() == expected


def test_remote_deletions_are_reported_without_deleting_local_files(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").unlink()
    collect_sync(spec, workspace, run_dir)
    report = apply_sync(config.project, run_dir)
    assert report["deleted_remote"] == ["data/config.json"]
    assert (config.project / "data/config.json").read_text() == "original"


def test_local_deletion_is_a_conflict(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").write_text("remote")
    (config.project / "data/config.json").unlink()
    collect_sync(spec, workspace, run_dir)
    with pytest.raises(SyncConflict):
        apply_sync(config.project, run_dir)
    assert not (config.project / "data/config.json").exists()


def test_new_remote_file_cannot_overwrite_new_local_file(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/new.json").write_text("remote new")
    (config.project / "data/new.json").write_text("local new")
    collect_sync(spec, workspace, run_dir)
    with pytest.raises(SyncConflict):
        apply_sync(config.project, run_dir)


def test_corrupt_or_unselected_download_is_rejected(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").write_text("remote")
    collect_sync(spec, workspace, run_dir)
    with pytest.raises(ValueError, match="selection"):
        apply_sync(config.project, run_dir, selections=["other"])
    (run_dir / "_sync/files/data/config.json").write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        apply_sync(config.project, run_dir)
    assert (config.project / "data/config.json").read_text() == "original"


def test_sync_rejects_symlink_destination(sync_setup, tmp_path):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").write_text("remote")
    collect_sync(spec, workspace, run_dir)
    target = config.project / "data/config.json"
    target.unlink()
    outside = tmp_path / "secret"
    outside.write_text("safe")
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        apply_sync(config.project, run_dir)
    assert outside.read_text() == "safe"


def test_ignored_and_symlinked_remote_files_are_not_harvested(sync_setup, tmp_path):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/private.key").write_text("private")
    (workspace / "data/link.json").symlink_to(config.project / "main.py")
    collect_sync(spec, workspace, run_dir)
    assert json.loads((run_dir / "_sync/manifest.json").read_text())["files"] == []


def test_mode_only_changes_round_trip(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/config.json").chmod(0o755)
    collect_sync(spec, workspace, run_dir)
    assert apply_sync(config.project, run_dir)["applied"] == ["data/config.json"]
    assert (config.project / "data/config.json").stat().st_mode & 0o111


def test_directory_collision_cannot_be_silently_overwritten(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    (workspace / "data/new.json").write_text("remote")
    (config.project / "data/new.json").mkdir()
    collect_sync(spec, workspace, run_dir)
    with pytest.raises(SyncConflict):
        apply_sync(config.project, run_dir, conflict="remote")


def test_invalid_sync_manifest_fails_before_changes(sync_setup):
    config, workspace, run_dir, spec = sync_setup
    collect_sync(spec, workspace, run_dir)
    (run_dir / "_sync/manifest.json").write_text('{"schema_version": 1}')
    with pytest.raises(ValueError, match="invalid sync manifest"):
        apply_sync(config.project, run_dir)


def test_explicitly_unignored_hidden_config_can_round_trip(config_factory, project, tmp_path):
    (project / ".settings.json").write_text("original")
    (project / ".modalignore").write_text("!.settings.json\n")
    config = config_factory({"sync": {"bidirectional": [".settings.json"]}})
    frozen = snapshot(config, tmp_path / "snapshot")
    workspace, run_dir = tmp_path / "remote", tmp_path / "artifacts"
    shutil.copytree(frozen.project, workspace)
    (workspace / ".settings.json").write_text("remote")
    collect_sync(make_spec(config, frozen, "test-run"), workspace, run_dir)
    assert apply_sync(config.project, run_dir, selections=[".settings.json"])["applied"] == [
        ".settings.json"
    ]
    assert (project / ".settings.json").read_text() == "remote"
