"""Infrastructure adapter. Workloads never import this module or Modal."""

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from sync_manifest import inside, output_paths


def kill_group(process):
    if process is not None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_workload(spec, source, workspace, artifacts, cache):
    """Run an ordinary argv subprocess; preserve outputs on failure and timeout."""
    source, workspace, artifacts, cache = map(
        Path, (source, workspace, artifacts, cache)
    )
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, workspace, dirs_exist_ok=True)
    metadata = artifacts / "_runner"
    metadata.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    for relative in output_paths(spec["output_dirs"]):
        link = inside(workspace, str(relative))
        target = inside(artifacts, str(relative))
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        # Upload policy removes these paths: never silently discard source files.
        link.symlink_to(target, target_is_directory=True)
    (metadata / "source.json").write_text(json.dumps(spec["source"], indent=2) + "\n")
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(workspace),
            "HF_HOME": str(cache / "huggingface"),
            "XDG_CACHE_HOME": str(cache),
            "TORCH_HOME": str(cache / "torch"),
        }
    )
    started = time.monotonic()
    process = None
    result = {**spec, "status": "failed", "returncode": 1}
    try:
        with (
            (metadata / "console.log").open("wb") as log,
            selectors.DefaultSelector() as selector,
        ):
            process = subprocess.Popen(
                spec["command"],
                cwd=workspace,
                env=env,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            selector.register(process.stdout, selectors.EVENT_READ)
            timed_out = False
            last_heartbeat = started
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if now - started >= spec["timeout_seconds"]:
                    timed_out = True
                    kill_group(process)
                if process.poll() is not None:
                    kill_group(process)  # close inherited pipes held by orphan workers
                for key, _ in selector.select(timeout=0.2):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        log.write(data)
                        log.flush()
                        # Decode only for display; the downloaded log preserves exact bytes.
                        sys.stdout.write(data.decode("utf-8", errors="replace"))
                        sys.stdout.flush()
                if now - last_heartbeat >= 30:
                    print(f"[runner] running for {now - started:.0f}s", flush=True)
                    last_heartbeat = now
            code = process.wait()
            process.stdout.close()
            result["returncode"] = (
                124 if timed_out else (128 - code if code < 0 else code)
            )
            result["status"] = (
                "timed_out" if timed_out else ("completed" if code == 0 else "failed")
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
        with (metadata / "console.log").open("a") as log:
            log.write(traceback.format_exc())
    finally:
        kill_group(process)
        if process is not None:
            process.wait()
            if process.stdout:
                process.stdout.close()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (metadata / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def execute(spec):
    """Modal entrypoint, deliberately separate from the user's Python program."""
    import modal

    cache = modal.Volume.from_name(spec["cache_volume"])
    artifacts = modal.Volume.from_name(spec["artifact_volume"])
    print(f"[runner] starting {spec['run_id']}: {spec['command']}", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="gpu-starter-") as workspace:
            return run_workload(
                spec,
                "/opt/gpu-starter/project",
                workspace,
                Path("/artifacts/runs") / spec["run_id"],
                "/cache",
            )
    finally:
        # Even Python exceptions and handled command timeouts persist partial outputs.
        # Commit artifacts first so a cache error cannot hide a completed workload.
        artifacts.commit()
        cache.commit()
