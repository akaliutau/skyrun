"""Modal transport entry point. No workload code imports this module."""

import hashlib
import json
import queue
import tempfile
import time
import traceback
from pathlib import Path

from skyrun.paths import atomic_write, inside, validate_run_id
from skyrun.remote.engine import run_workload

ARTIFACT_ROOT = Path("/artifacts/runs")
SOURCE_BLOBS = Path("/source/blobs")
CACHE_ROOT = Path("/cache")


def _verified_chunks(source, digest, expected_size, name):
    fingerprint, size = hashlib.sha256(), 0
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            fingerprint.update(chunk)
            size += len(chunk)
            yield chunk
    if fingerprint.hexdigest() != digest or size != expected_size:
        raise ValueError(f"cached source failed integrity check: {name}")


def materialize_source(manifest, blobs, destination):
    """Reconstruct and verify an immutable source snapshot from content-addressed blobs."""
    canonical = json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != manifest["sha256"]:
        raise ValueError("source manifest failed integrity check")
    seen = set()
    for entry in manifest["files"]:
        name, digest = entry["path"], entry["sha256"]
        if name in seen or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid source manifest entry")
        seen.add(name)
        target = inside(destination, name, allow_hidden=True)
        source = inside(blobs, digest)
        if entry["mode"] not in {0o644, 0o755}:
            raise ValueError("invalid source file mode")
        atomic_write(target, _verified_chunks(source, digest, entry["bytes"], name))
        target.chmod(entry["mode"])


def execute(spec, channel):
    """Handshake before starting; always commit logs and saved files after execution."""
    import modal

    environment = spec.get("modal_environment")
    cache = modal.Volume.from_name(spec["cache_volume"], environment_name=environment)
    artifacts = modal.Volume.from_name(spec["artifact_volume"], environment_name=environment)
    # Modal can implement the trusted mountpoints themselves as symlinks. Resolve
    # only those mount roots, then reject links in every user-controlled subpath.
    artifact_root = inside(ARTIFACT_ROOT.parent.resolve(), ARTIFACT_ROOT.name)
    artifact_path = inside(artifact_root, validate_run_id(spec["run_id"]))
    source_blobs = inside(SOURCE_BLOBS.parent.resolve(), SOURCE_BLOBS.name)
    cache_root = CACHE_ROOT.resolve()
    started = time.monotonic()
    try:
        channel.put("ready", partition="events")
        try:
            initial_command = channel.get(timeout=60, partition="commands")
        except queue.Empty:
            initial_command = "interrupt"

        def poll_control():
            if initial_command != "start":
                return "interrupt"
            commands = channel.get_many(16, block=False, partition="commands")
            return (
                "kill" if "kill" in commands else "interrupt" if "interrupt" in commands else None
            )

        def emit(data):
            try:
                channel.put(data, block=False, partition="output")
                return True
            except queue.Full:
                return False

        with tempfile.TemporaryDirectory(prefix="skyrun-") as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            if initial_command == "start":
                materialize_source(spec["source"], source_blobs, source)
            return run_workload(
                spec,
                source,
                root / "workspace",
                artifact_path,
                cache_root,
                poll_control=poll_control,
                emit=emit,
            )
    except Exception as error:
        # Transport/setup failures also leave a recoverable diagnostic record.
        metadata = artifact_path / "_runner"
        metadata.mkdir(parents=True, exist_ok=True)
        result = {
            **spec,
            "status": "failed",
            "returncode": 1,
            "started": False,
            "forced": False,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(error).__name__}: {error}",
        }
        with (metadata / "console.log").open("ab") as log:
            log.write(traceback.format_exc().encode())
        atomic_write(metadata / "result.json", [json.dumps(result, indent=2).encode() + b"\n"])
        return result
    finally:
        try:
            artifacts.commit()
        finally:
            cache.commit()
