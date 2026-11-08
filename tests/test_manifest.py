from io import BytesIO
from zipfile import ZipFile

import pytest

from skyrun.manifest import snapshot
from skyrun.remote.app import materialize_source


def put(root, name, content="private"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_policy_scans_only_selected_folder(config_factory, project, tmp_path):
    put(project, "src/work.py", "print('work')")
    put(project, "private.txt")
    for name in [
        "src/.env",
        "src/.git/config",
        "src/__pycache__/a.pyc",
        "src/weights/model.bin",
        "src/MODEL.PT",
        "src/outputs/a.txt",
        "src/Answers_Solutions.py",
    ]:
        put(project, name)
    config = config_factory({"sync": {"project_dir": "src"}})
    frozen = snapshot(config, tmp_path / "snapshot")
    assert [e["path"] for e in frozen.manifest["files"]] == ["work.py"]
    assert frozen.manifest["bytes"] == len("print('work')")


def test_ignore_rules_support_negation_and_cannot_include_git(config_factory, project, tmp_path):
    for name in ["data/keep.csv", "data/skip.csv", ".settings.json", ".git/config"]:
        put(project, name)
    put(project, ".modalignore", "data/*\n!data/keep.csv\n!.settings.json\n!.git/\n!.git/**\n")
    frozen = snapshot(config_factory(), tmp_path / "snapshot")
    assert {e["path"] for e in frozen.manifest["files"]} == {
        "main.py",
        "data/keep.csv",
        ".settings.json",
    }


def test_frozen_hashes_and_archive_are_deterministic(config_factory, project, tmp_path):
    first = snapshot(config_factory(), tmp_path / "first")
    second = snapshot(config_factory(), tmp_path / "second")
    assert first.manifest == second.manifest
    assert first.archive() == second.archive()
    original = first.archive()
    (project / "main.py").write_text("changed")
    assert first.archive() == original
    assert (
        snapshot(config_factory(), tmp_path / "third").manifest["sha256"]
        != first.manifest["sha256"]
    )
    with ZipFile(BytesIO(original)) as archive:
        assert archive.namelist() == ["main.py"]
        assert archive.read("main.py") == b"print('hello')\n"


def test_executable_modes_are_fingerprinted_and_restored(config_factory, project, tmp_path):
    script = put(project, "command", "#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    frozen = snapshot(config_factory(), tmp_path / "snapshot")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    for entry in frozen.manifest["files"]:
        (blobs / entry["sha256"]).write_bytes((frozen.project / entry["path"]).read_bytes())
    restored = tmp_path / "restored"
    materialize_source(frozen.manifest, blobs, restored)
    assert (restored / "command").stat().st_mode & 0o111
    before = frozen.manifest["sha256"]
    script.chmod(0o644)
    assert snapshot(config_factory(), tmp_path / "other").manifest["sha256"] != before


def test_cache_corruption_is_rejected(config_factory, tmp_path):
    frozen = snapshot(config_factory(), tmp_path / "snapshot")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    entry = frozen.manifest["files"][0]
    (blobs / entry["sha256"]).write_bytes(b"corrupt")
    restored = tmp_path / "restored"
    with pytest.raises(ValueError, match="integrity"):
        materialize_source(frozen.manifest, blobs, restored)
    assert not (restored / entry["path"]).exists()


def test_symlinks_are_never_uploaded(config_factory, project, tmp_path):
    secret = put(tmp_path, "secret", "secret")
    (project / "linked.py").symlink_to(secret)
    (project / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    frozen = snapshot(config_factory(), tmp_path / "snapshot")
    assert [e["path"] for e in frozen.manifest["files"]] == ["main.py"]


def test_symlink_ignore_file_rejected(config_factory, project, tmp_path):
    external = put(tmp_path, "ignore", "!**")
    (project / ".modalignore").symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        snapshot(config_factory(), tmp_path / "snapshot")


def test_file_and_byte_budgets(config_factory, project, tmp_path):
    put(project, "second.py", "pass")
    with pytest.raises(ValueError, match="max_upload_files"):
        snapshot(config_factory({"sync": {"max_upload_files": 1}}), tmp_path / "files")
    (project / "second.py").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="upload budget"):
        snapshot(config_factory({"sync": {"max_upload_mib": 1}}), tmp_path / "bytes")


def test_explicit_output_root_is_not_reuploaded(config_factory, project, tmp_path):
    extra = project / "my-runs"
    put(extra, "old-run/file.txt")
    frozen = snapshot(config_factory(), tmp_path / "snapshot", exclude_roots=[extra])
    assert [e["path"] for e in frozen.manifest["files"]] == ["main.py"]


def test_requirements_are_frozen_separately(config_factory, project, tmp_path):
    path = put(project, "deps.txt", "numpy==2.2.6\n")
    frozen = snapshot(
        config_factory({"image": {"requirements": "deps.txt"}}), tmp_path / "snapshot"
    )
    path.write_text("changed")
    assert frozen.requirements.read_text() == "numpy==2.2.6\n"


def test_no_uploadable_files(config_factory, project, tmp_path):
    (project / "main.py").unlink()
    with pytest.raises(ValueError, match="no uploadable"):
        snapshot(config_factory(), tmp_path / "snapshot")


def test_git_metadata_cannot_be_selected_as_project_root(config_factory, project, tmp_path):
    put(project, ".git/config", "private Git configuration")
    with pytest.raises(ValueError, match="Git metadata"):
        snapshot(config_factory({"sync": {"project_dir": ".git"}}), tmp_path / "snapshot")
