"""Provider-neutral local orchestration and public Python API."""

import datetime
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from skyrun import __version__
from skyrun.config import Config, load_config
from skyrun.manifest import file_hash, snapshot
from skyrun.paths import atomic_write, checked_root, inside, validate_run_id
from skyrun.providers import get_provider
from skyrun.sync import apply_sync


@dataclass(frozen=True)
class RunResult:
    run_id: str
    directory: Path
    data: dict
    sync_report: dict | None = None

    @property
    def returncode(self):
        return self.data["returncode"]

    @property
    def status(self):
        return self.data["status"]


def _config(value):
    return value if isinstance(value, Config) else load_config(value)


def _local_root(config, output_dir):
    return (
        checked_root(output_dir)
        if output_dir is not None
        else inside(config.root, config["sync"]["local_dir"], allow_hidden=True)
    )


def make_spec(config, source, run_id):
    run, modal, workload = config["run"], config["modal"], config["workload"]
    terminal = shutil.get_terminal_size(fallback=(100, 24))
    return {
        "schema_version": 1,
        "skyrun_version": __version__,
        "run_id": run_id,
        "provider": config["provider"],
        "provider_options": config["provider_options"],
        "source": source.manifest,
        "command": workload["command"],
        "output_dirs": config["sync"]["output_dirs"],
        "bidirectional": config["sync"]["bidirectional"],
        "sync_baseline": source.sync_baseline,
        "ignore_patterns": list(source.ignore_patterns),
        "project_root": str(config.project),
        "timeout_seconds": run["timeout_seconds"],
        "startup_timeout_seconds": run["startup_timeout_seconds"],
        "shutdown_grace_seconds": run["shutdown_grace_seconds"],
        "terminal": workload["terminal"],
        "terminal_columns": terminal.columns,
        "terminal_rows": terminal.lines,
        "gpu_request": None if run["gpu"].lower() in {"cpu", "none"} else run["gpu"],
        "cpu": run["cpu"],
        "memory_mib": run["memory_mib"],
        "cache_volume": modal["cache_volume"],
        "artifact_volume": modal["artifact_volume"],
        "source_volume": modal["source_volume"],
        "modal_environment": modal["environment_name"],
        "environment": config["environment"],
        "image": config["image"],
        "requirements_sha256": file_hash(source.requirements) if source.requirements else None,
    }


def run(
    config=None,
    *,
    command=None,
    gpu=None,
    timeout=None,
    project_dir=None,
    output_dir=None,
    provider=None,
    sync=True,
    conflict="error",
) -> RunResult:
    """Run a frozen project snapshot; download saved files and optionally apply selected changes."""
    config = _config(config).override(
        gpu=gpu, timeout=timeout, command=command, provider=provider, project_dir=project_dir
    )
    local = _local_root(config, output_dir)
    if conflict not in {"error", "local", "remote"}:
        raise ValueError("sync conflict policy must be error, local, or remote")
    adapter = get_provider(config["provider"])
    with tempfile.TemporaryDirectory(prefix="skyrun-upload-") as directory:
        source = snapshot(config, directory, exclude_roots=[local])
        run_id = (
            datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        )
        destination = inside(local, run_id)
        destination.mkdir(parents=True, exist_ok=False)
        spec = make_spec(config, source, run_id)
        atomic_write(destination / "submitted.json", [json.dumps(spec, indent=2).encode() + b"\n"])
        print(
            f"Run {run_id}: {len(source.manifest['files'])} source files, {source.manifest['bytes']:,} bytes; GPU {spec['gpu_request'] or 'cpu'}",
            flush=True,
        )
        print(
            f"Outputs: {destination}\nRecovery: use --download {run_id} with the same launcher and configuration.",
            flush=True,
        )
        result = adapter.run(config, source, spec, destination)
        atomic_write(destination / "result.json", [json.dumps(result, indent=2).encode() + b"\n"])
        report = None
        if not result.get("canceled_before_start"):
            adapter.download(config, run_id, destination)
            if sync and config["sync"]["bidirectional"]:
                report = apply_sync(
                    config.project, destination, conflict=conflict, selections=spec["bidirectional"]
                )
        print(
            f"{result['status']}: exit {result['returncode']}, {result['elapsed_seconds']}s",
            flush=True,
        )
        return RunResult(run_id, destination, result, report)


def download(run_id, config=None, *, output_dir=None, sync=False, conflict="error") -> Path:
    """Recover committed files without allocating compute; sync requires explicit opt-in."""
    config = _config(config)
    run_id = validate_run_id(run_id)
    destination = inside(_local_root(config, output_dir), run_id)
    adapter = get_provider(config["provider"])
    adapter.download(config, run_id, destination)
    if sync:
        apply_sync(
            config.project,
            destination,
            conflict=conflict,
            selections=config["sync"]["bidirectional"],
        )
    return destination
