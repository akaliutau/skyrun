"""Exercise project coordination using independent OS processes, not thread mocks."""

import errno
import json
import multiprocessing
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyrun import locking
from skyrun.errors import SyncConflict
from skyrun.manifest import snapshot
from skyrun.paths import PROJECT_LOCK_NAME
from skyrun.runner import make_spec, run
from skyrun.sync import apply_sync, collect_sync


def _probe_lock(project):
    """Try the actual OS lock without blocking; return whether it was available."""
    if os.name == "nt":
        import msvcrt

        fd = os.open(project / PROJECT_LOCK_NAME, os.O_RDWR)
        try:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                return False
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return True
        finally:
            os.close(fd)
    else:
        import fcntl

        fd = os.open(project, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        finally:
            os.close(fd)


def _hold_project(project, ready, release):
    with locking.project_lock(project):
        ready.set()
        if not release.wait(20):
            raise TimeoutError("test did not release project lock")


def _apply_worker(project, artifacts, phase, ready, release, attempted, acquired, results):
    from skyrun import paths, sync

    original_lock = sync.project_lock

    @contextmanager
    def announced_lock(path):
        attempted.set()
        with original_lock(path):
            acquired.set()
            yield

    sync.project_lock = announced_lock
    paused = False

    def pause_once():
        nonlocal paused
        if not paused:
            paused = True
            ready.set()
            if not release.wait(20):
                raise TimeoutError("test did not release sync writer")

    if phase == "preflight":
        original_version = sync._local_version

        def version(path):
            pause_once()
            return original_version(path)

        sync._local_version = version
    elif phase == "replace":
        original_replace = paths.os.replace

        def replace(source, destination):
            if Path(destination) == project / "state.json":
                pause_once()
            return original_replace(source, destination)

        paths.os.replace = replace
    try:
        report = apply_sync(project, artifacts, conflict="error")
        results.put({"status": "applied", **report})
    except SyncConflict as error:
        results.put({"status": "conflict", "paths": list(error.paths)})


def _snapshot_worker(config, destination, ready, release):
    from skyrun import manifest

    original_freeze = manifest.freeze_file
    paused = False

    def freeze(*args):
        nonlocal paused
        if not paused:
            paused = True
            ready.set()
            if not release.wait(20):
                raise TimeoutError("test did not release snapshot reader")
        return original_freeze(*args)

    manifest.freeze_file = freeze
    snapshot(config, destination)


def _stop(process):
    if process.pid is not None:
        if process.is_alive():
            process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=10)


def _join_success(process):
    process.join(timeout=15)
    assert not process.is_alive(), "project operation remained blocked"
    assert process.exitcode == 0


@pytest.fixture
def process_context():
    # Spawn does not inherit a parent's open lock descriptor and works on Windows.
    return multiprocessing.get_context("spawn")


@pytest.mark.parametrize("phase", ["preflight", "replace"])
def test_competing_sync_writers_report_a_conflict(
    config_factory, project, tmp_path, process_context, phase
):
    (project / "state.json").write_text("original")
    config = config_factory({"sync": {"bidirectional": ["state.json"]}})
    frozen = snapshot(config, tmp_path / "initial")
    artifacts = []
    for name in ("A", "B"):
        workspace, run_directory = tmp_path / ("workspace-" + name), tmp_path / name
        shutil.copytree(frozen.project, workspace)
        (workspace / "state.json").write_text(name)
        collect_sync(make_spec(config, frozen, name), workspace, run_directory)
        artifacts.append(run_directory)
    ctx = process_context
    ready, release, attempted_a, acquired_a = (ctx.Event() for _ in range(4))
    attempted_b, acquired_b, results = ctx.Event(), ctx.Event(), ctx.Queue()
    first = ctx.Process(
        target=_apply_worker,
        args=(project, artifacts[0], phase, ready, release, attempted_a, acquired_a, results),
    )
    second = ctx.Process(
        target=_apply_worker,
        args=(project, artifacts[1], None, None, None, attempted_b, acquired_b, results),
    )
    try:
        first.start()
        assert ready.wait(15), "first writer did not reach its critical section"
        assert not _probe_lock(project), "sync does not hold the OS project lock"
        second.start()
        assert attempted_b.wait(15), "second writer did not attempt to apply"
        assert not acquired_b.is_set(), "second writer entered before the first finished"
        release.set()
        _join_success(first)
        _join_success(second)
        reports = [results.get(timeout=2), results.get(timeout=2)]
        assert sorted(report["status"] for report in reports) == ["applied", "conflict"]
        assert (project / "state.json").read_text() == "A"
        assert json.loads((artifacts[1] / "sync.json").read_text())["conflicts"] == ["state.json"]
        assert (artifacts[1] / "_sync/files/state.json").read_text() == "B"
        assert _probe_lock(project), "project lock leaked after sync completion/conflict"
    finally:
        release.set()
        _stop(first)
        _stop(second)
        results.close()


def test_snapshot_coordinates_with_sync_writers(config_factory, project, tmp_path, process_context):
    (project / "state.json").write_text("original")
    config = config_factory({"sync": {"bidirectional": ["state.json"]}})
    initial = snapshot(config, tmp_path / "initial")
    workspace, artifacts = tmp_path / "workspace", tmp_path / "artifacts"
    shutil.copytree(initial.project, workspace)
    (workspace / "state.json").write_text("remote")
    collect_sync(make_spec(config, initial, "writer"), workspace, artifacts)
    ctx = process_context
    ready, release, attempted, acquired = (ctx.Event() for _ in range(4))
    results = ctx.Queue()
    destination = tmp_path / "captured"
    reader = ctx.Process(target=_snapshot_worker, args=(config, destination, ready, release))
    writer = ctx.Process(
        target=_apply_worker,
        args=(project, artifacts, None, None, None, attempted, acquired, results),
    )
    try:
        reader.start()
        assert ready.wait(15), "snapshot did not start copying"
        assert not _probe_lock(project), "snapshot does not hold the OS project lock"
        writer.start()
        assert attempted.wait(15), "writer did not attempt to apply"
        assert not acquired.is_set(), "writer changed the project during snapshot copying"
        release.set()
        _join_success(reader)
        _join_success(writer)
        assert results.get(timeout=2)["status"] == "applied"
        assert (destination / "project/state.json").read_text() == "original"
        assert (project / "state.json").read_text() == "remote"
    finally:
        release.set()
        _stop(reader)
        _stop(writer)
        results.close()


def test_different_projects_do_not_block_each_other(project, tmp_path, process_context):
    other = tmp_path / "other"
    other.mkdir()
    ctx = process_context
    first_ready, second_ready, release = ctx.Event(), ctx.Event(), ctx.Event()
    first = ctx.Process(target=_hold_project, args=(project, first_ready, release))
    second = ctx.Process(target=_hold_project, args=(other, second_ready, release))
    try:
        first.start()
        assert first_ready.wait(15)
        second.start()
        assert second_ready.wait(15), "an unrelated project is blocked"
        assert not _probe_lock(project) and not _probe_lock(other)
        release.set()
        _join_success(first)
        _join_success(second)
    finally:
        release.set()
        _stop(first)
        _stop(second)


def test_process_termination_releases_project_lock(project, process_context):
    ready, release = process_context.Event(), process_context.Event()
    owner = process_context.Process(target=_hold_project, args=(project, ready, release))
    try:
        owner.start()
        assert ready.wait(15)
        assert not _probe_lock(project)
        owner.terminate()
        owner.join(timeout=10)
        assert not owner.is_alive()
        assert _probe_lock(project), "terminated process left a stale lock"
    finally:
        _stop(owner)


def test_exception_releases_project_lock(project):
    with pytest.raises(RuntimeError, match="failed operation"), locking.project_lock(project):
        assert not _probe_lock(project)
        raise RuntimeError("failed operation")
    assert _probe_lock(project)


@pytest.mark.skipif(os.name == "nt", reason="POSIX uses directory locks")
def test_read_only_source_needs_no_sidecar_file(config_factory, project, tmp_path):
    project.chmod(0o555)
    try:
        frozen = snapshot(config_factory(), tmp_path / "snapshot")
        assert [entry["path"] for entry in frozen.manifest["files"]] == ["main.py"]
        assert not (project / PROJECT_LOCK_NAME).exists()
    finally:
        project.chmod(0o755)


def test_cloud_execution_and_download_do_not_hold_project_lock(config_factory, monkeypatch):
    class Provider:
        def run(self, config, source, spec, destination):
            assert _probe_lock(config.project), "lock held while cloud workload runs"
            return {"status": "completed", "returncode": 0, "elapsed_seconds": 0}

        def download(self, config, run_id, destination):
            assert _probe_lock(config.project), "lock held while downloading artifacts"
            return 0

    monkeypatch.setattr("skyrun.runner.get_provider", lambda name: Provider())
    assert run(config_factory()).returncode == 0


def test_windows_lock_retries_contention_and_surfaces_other_errors(tmp_path, monkeypatch):
    attempts, sleeps = [], []

    def acquire(fd, mode, length):
        attempts.append((os.lseek(fd, 0, os.SEEK_CUR), mode, length))
        if len(attempts) < 3:
            raise OSError(errno.EACCES, "another process holds the lock")

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=1, locking=acquire))
    monkeypatch.setattr(locking.time, "sleep", sleeps.append)
    with (tmp_path / "lock").open("w+b") as stream:
        locking._lock_windows(stream.fileno())
    assert attempts == [(0, 1, 1)] * 3
    assert sleeps == [0.05, 0.05]

    def bad_descriptor(*args):
        raise OSError(errno.EBADF, "invalid descriptor")

    monkeypatch.setattr(sys.modules["msvcrt"], "locking", bad_descriptor)
    with (tmp_path / "lock").open("r+b") as stream, pytest.raises(OSError) as error:
        locking._lock_windows(stream.fileno())
    assert error.value.errno == errno.EBADF
