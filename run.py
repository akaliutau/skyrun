"""python run.py [launcher options] -- python train.py [workload options]"""

import argparse
import datetime
import json
import re
import tempfile
import time
import tomllib
import uuid
from pathlib import Path, PurePosixPath

from sync_manifest import inside, output_paths, relative_path, snapshot

ROOT = Path(__file__).resolve().parent


def download(volume, run_id, destination):
    """Stream volume files to disk, using atomic replacements and safe local paths."""
    from modal.volume import FileEntryType

    remote = PurePosixPath("runs") / run_id
    destination = Path(destination).resolve()
    count = 0
    for entry in volume.iterdir(str(remote), recursive=True):
        if entry.type != FileEntryType.FILE:
            continue
        relative = PurePosixPath(entry.path.lstrip("/")).relative_to(remote)
        target = inside(destination, str(relative), allow_hidden=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".download")
        if temporary.is_symlink():
            raise ValueError(f"symlink download destination: {temporary}")
        with temporary.open("wb") as stream:
            for chunk in volume.read_file(entry.path):
                stream.write(chunk)
        temporary.replace(target)
        count += 1
    if not count:
        raise FileNotFoundError(
            f"no committed files in {remote}; check the run ID and Modal environment"
        )
    print(f"Downloaded {count} files to {destination}", flush=True)


def validate(config):
    settings = config["modal"]
    output_paths(config["sync"]["output_dirs"])
    relative_path(config["sync"]["project_dir"])
    relative_path(config["sync"]["local_dir"])
    timeout = settings["timeout_seconds"]
    if not isinstance(timeout, int) or not 1 <= timeout <= 86280:
        raise ValueError("timeout_seconds must be an integer from 1 to 86280")
    if (
        settings["cpu"] <= 0
        or settings["memory_mib"] <= 0
        or settings["startup_timeout_seconds"] <= 0
    ):
        raise ValueError("CPU, memory and startup timeout must be positive")
    command = config["workload"]["command"]
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(x, str) and x for x in command)
    ):
        raise ValueError("workload.command must be a nonempty argv list")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "gpu.toml")
    parser.add_argument(
        "--gpu", help="Modal GPU type/count, e.g. T4 or H100:2; cpu for no GPU"
    )
    parser.add_argument("--timeout", type=int, help="workload timeout in seconds")
    parser.add_argument(
        "--output-dir", type=Path, help="local run parent (default: runs/)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print exact upload manifest without contacting Modal",
    )
    parser.add_argument(
        "--download", metavar="RUN_ID", help="recover outputs without starting a GPU"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    root = config_path.parent
    with config_path.open("rb") as stream:
        config = tomllib.load(stream)
    settings = config["modal"]
    if args.gpu:
        settings["gpu"] = args.gpu
    if args.timeout is not None:
        settings["timeout_seconds"] = args.timeout
    if args.command:
        command = args.command[1:] if args.command[0] == "--" else args.command
        config["workload"]["command"] = command
    validate(config)
    local_root = (
        args.output_dir
        if args.output_dir is not None
        else inside(root, config["sync"]["local_dir"])
    )
    if local_root.is_symlink():
        raise ValueError("local output parent cannot be a symlink")
    if args.download:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.download):
            raise ValueError("invalid run ID")
        import modal

        volume = modal.Volume.from_name(settings["artifact_volume"])
        download(volume, args.download, inside(local_root, args.download))
        return 0
    with tempfile.TemporaryDirectory(prefix="gpu-starter-upload-") as directory:
        source = snapshot(root, config, directory)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "source": source,
                        "gpu": settings["gpu"],
                        "command": config["workload"]["command"],
                    },
                    indent=2,
                )
            )
            return 0
        import modal
        from modal_runner import execute

        stage = Path(directory)
        image = modal.Image.debian_slim(
            python_version=config["image"]["python_version"]
        )
        if config["image"]["apt_packages"]:
            image = image.apt_install(*config["image"]["apt_packages"])
        image = image.pip_install_from_requirements(str(stage / "requirements-gpu.txt"))
        image = image.env({"PYTHONPATH": "/opt/gpu-starter", "PYTHONUNBUFFERED": "1"})
        for file in source["files"]:
            image = image.add_local_file(
                stage / file["path"], "/opt/gpu-starter/" + file["path"]
            )
        gpu = None if settings["gpu"].lower() in {"cpu", "none"} else settings["gpu"]
        cache = modal.Volume.from_name(settings["cache_volume"], create_if_missing=True)
        artifacts = modal.Volume.from_name(
            settings["artifact_volume"], create_if_missing=True
        )
        app = modal.App(settings["app_name"], include_source=False)
        function = app.function(
            image=image,
            gpu=gpu,
            cpu=settings["cpu"],
            memory=settings["memory_mib"],
            timeout=settings["timeout_seconds"] + 120,
            startup_timeout=settings["startup_timeout_seconds"],
            volumes={"/cache": cache, "/artifacts": artifacts},
            max_containers=1,
            scaledown_window=2,
            retries=0,
            include_source=False,
        )(execute)
        run_id = (
            datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ-")
            + uuid.uuid4().hex[:8]
        )
        destination = inside(local_root, run_id)
        destination.mkdir(parents=True)
        spec = {
            "run_id": run_id,
            "source": source,
            "command": config["workload"]["command"],
            "output_dirs": config["sync"]["output_dirs"],
            "timeout_seconds": settings["timeout_seconds"],
            "gpu_request": gpu,
            "cpu": settings["cpu"],
            "memory_mib": settings["memory_mib"],
            "cache_volume": settings["cache_volume"],
            "artifact_volume": settings["artifact_volume"],
        }
        (destination / "submitted.json").write_text(json.dumps(spec, indent=2) + "\n")
        print(
            f"Run {run_id}: {len(source['files'])} source files, {source['bytes']:,} bytes; GPU {gpu or 'cpu'}",
            flush=True,
        )
        print(
            f"Outputs: {destination}\nRecovery: python run.py --download {run_id}",
            flush=True,
        )
        result = None
        with modal.enable_output(), app.run():
            call = function.spawn(spec)
            (destination / "call.json").write_text(
                json.dumps({"call_id": call.object_id}, indent=2) + "\n"
            )
            finished = False
            deadline = (
                time.monotonic()
                + settings["timeout_seconds"]
                + settings["startup_timeout_seconds"]
                + 180
            )
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("queue/startup/workload wait limit exceeded")
                    try:
                        result = call.get(timeout=min(10, remaining))
                        finished = True
                        break
                    except TimeoutError:
                        print(f"[launcher] waiting for {run_id}", flush=True)
            finally:
                if not finished:
                    call.cancel(terminate_containers=True)
                    (destination / "interrupted.json").write_text(
                        json.dumps(
                            {"status": "interrupted", "call_id": call.object_id},
                            indent=2,
                        )
                        + "\n"
                    )
                    print(
                        f"Run interrupted. Recover last committed files with --download {run_id}",
                        flush=True,
                    )
            # Download even when the workload returned a failure or timed out.
            (destination / "result.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            download(artifacts, run_id, destination)
        # Modal's app context can suppress KeyboardInterrupt after stopping the app.
        if result is None:
            return 130
        print(
            f"{result['status']}: exit {result['returncode']}, {result['elapsed_seconds']}s",
            flush=True,
        )
        return result["returncode"]


if __name__ == "__main__":
    raise SystemExit(main())
