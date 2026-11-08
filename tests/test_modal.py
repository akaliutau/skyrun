import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

modal = pytest.importorskip("modal")

from modal.volume import FileEntryType

from skyrun.manifest import snapshot
from skyrun.providers.modal import download_volume, upload_source


class Volume:
    def __init__(self):
        self.files = {}
        self.uploads = []

    def iterdir(self, path, *, recursive):
        return [
            SimpleNamespace(path="/" + name, type=FileEntryType.FILE, size=len(content))
            for name, content in self.files.items()
            if name.startswith(path + "/")
        ]

    def read_file(self, name):
        content = self.files[name.lstrip("/")]
        for i in range(0, len(content), 3):
            yield content[i : i + 3]

    @contextlib.contextmanager
    def batch_upload(self, *, force):
        yield self

    def put_file(self, source, destination):
        self.uploads.append(destination)
        self.files[destination.lstrip("/")] = Path(source).read_bytes()


def test_source_cache_uploads_only_changed_content(config_factory, project, tmp_path):
    volume = Volume()
    (project / "duplicate.py").write_bytes((project / "main.py").read_bytes())
    frozen = snapshot(config_factory(), tmp_path / "first")
    assert upload_source(volume, frozen) == 1
    assert upload_source(volume, frozen) == 0
    (project / "main.py").write_text("changed")
    changed = snapshot(config_factory(), tmp_path / "second")
    assert upload_source(volume, changed) == 1
    assert len(volume.uploads) == 2


def test_streamed_recovery_creates_only_requested_run(tmp_path):
    volume = Volume()
    volume.files = {
        "runs/one/outputs/result": b"large-ish data",
        "runs/two/outputs/result": b"other",
    }
    assert download_volume(volume, "one", tmp_path / "one") == 1
    assert (tmp_path / "one/outputs/result").read_bytes() == b"large-ish data"
    assert not (tmp_path / "two").exists()


def test_download_does_not_overwrite_local_metadata(tmp_path):
    volume = Volume()
    volume.files = {"runs/one/submitted.json": b"tampered"}
    destination = tmp_path / "one"
    destination.mkdir()
    (destination / "submitted.json").write_text("original")
    with pytest.raises(ValueError, match="collides"):
        download_volume(volume, "one", destination)
    assert (destination / "submitted.json").read_text() == "original"


def test_download_traversal_and_symlinks_are_rejected(tmp_path):
    volume = Volume()
    volume.files = {"runs/one/../escape": b"unsafe"}
    with pytest.raises(ValueError):
        download_volume(volume, "one", tmp_path / "one")
    volume.files = {"runs/one/outputs/result": b"unsafe"}
    (tmp_path / "one/outputs").mkdir(parents=True)
    external = tmp_path / "external"
    external.write_text("safe")
    (tmp_path / "one/outputs/result").symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        download_volume(volume, "one", tmp_path / "one")
    assert external.read_text() == "safe"


def test_interrupted_download_is_atomic(tmp_path):
    volume = Volume()
    volume.files = {"runs/one/result.txt": b"new"}

    def interrupted(name):
        yield b"partial"
        raise OSError("disconnected")

    volume.read_file = interrupted
    destination = tmp_path / "one"
    destination.mkdir()
    (destination / "result.txt").write_text("old")
    with pytest.raises(OSError, match="disconnected"):
        download_volume(volume, "one", destination)
    assert (destination / "result.txt").read_text() == "old"


def test_recovery_of_missing_run_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="committed files"):
        download_volume(Volume(), "missing", tmp_path)
