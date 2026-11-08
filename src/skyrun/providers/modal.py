"""Modal implementation; importing the package does not load or authenticate the SDK."""

import contextlib
import json
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from skyrun.errors import ConfigError
from skyrun.paths import atomic_write, checked_root, inside, validate_run_id
from skyrun.session import StopRequests, wait_for_result

LOCAL_METADATA = {"submitted.json", "call.json", "result.json", "interrupted.json", "sync.json"}
RUNTIME_FILES = (
    "__init__.py",
    "errors.py",
    "paths.py",
    "config.py",
    "manifest.py",
    "sync.py",
    "default.modalignore",
    "remote/__init__.py",
    "remote/app.py",
    "remote/engine.py",
)


def sdk():
    try:
        import modal
    except ImportError as error:
        raise ConfigError(
            "Modal support is not installed; run: python -m pip install 'skyrun[modal]'"
        ) from error
    return modal


def upload_source(volume, source):
    """Send each missing SHA-256 blob once, even when multiple source paths share it."""
    modal = sdk()
    try:
        existing = {
            PurePosixPath(entry.path).name: entry.size
            for entry in volume.iterdir("blobs", recursive=True)
        }
    except (FileNotFoundError, modal.exception.NotFoundError):
        existing = {}
    missing = {}
    for entry in source.manifest["files"]:
        if existing.get(entry["sha256"]) != entry["bytes"]:
            missing[entry["sha256"]] = source.project / entry["path"]
    if missing:
        with volume.batch_upload(force=True) as batch:
            for digest, path in sorted(missing.items()):
                batch.put_file(path, "/blobs/" + digest)
    return len(missing)


def download_volume(volume, run_id, destination):
    """Stream artifact bytes to unique temporary files; never follow local symlinks."""
    from modal.volume import FileEntryType

    remote = PurePosixPath("runs") / validate_run_id(run_id)
    destination = checked_root(destination)
    count = 0
    for entry in volume.iterdir(str(remote), recursive=True):
        if entry.type != FileEntryType.FILE:
            continue
        relative = PurePosixPath(entry.path.lstrip("/")).relative_to(remote)
        if relative.parts[0] in LOCAL_METADATA:
            raise ValueError(f"remote artifact collides with local run metadata: {relative}")
        target = inside(destination, str(relative), allow_hidden=True)
        atomic_write(target, volume.read_file(entry.path))
        count += 1
    if not count:
        raise FileNotFoundError(
            f"no committed files in {remote}; check the run ID, volume, and Modal environment"
        )
    print(f"Downloaded {count} files to {destination}", flush=True)
    return count


def build_image(config, source):
    modal = sdk()
    settings = config["image"]
    if settings["base_image"]:
        image = modal.Image.from_registry(
            settings["base_image"], add_python=settings["python_version"]
        )
    else:
        image = modal.Image.debian_slim(python_version=settings["python_version"])
    if settings["apt_packages"]:
        image = image.apt_install(*settings["apt_packages"])
    if source.requirements:
        image = image.pip_install_from_requirements(str(source.requirements))
    if settings["pip_packages"]:
        image = image.pip_install(*settings["pip_packages"])
    # Keep heavyweight workload dependency layers reusable when the runner's
    # ignore parser changes, and use the same parser version on both sides.
    image = image.pip_install("pathspec==" + version("pathspec"))
    image = image.env({"PYTHONPATH": "/opt/skyrun", "PYTHONUNBUFFERED": "1"})
    package = Path(__file__).resolve().parents[1]
    for name in RUNTIME_FILES:
        image = image.add_local_file(package / name, "/opt/skyrun/skyrun/" + name)
    return image


class ModalProvider:
    def download(self, config, run_id, destination):
        modal = sdk()
        settings = config["modal"]
        volume = modal.Volume.from_name(
            settings["artifact_volume"], environment_name=settings["environment_name"]
        )
        return download_volume(volume, run_id, destination)

    def run(self, config, source, spec, destination):
        modal = sdk()
        from skyrun.remote.app import execute

        settings, run = config["modal"], config["run"]
        environment = settings["environment_name"]
        volumes = {
            key: modal.Volume.from_name(
                settings[name], create_if_missing=True, environment_name=environment
            )
            for key, name in (
                ("/cache", "cache_volume"),
                ("/artifacts", "artifact_volume"),
                ("/source", "source_volume"),
            )
        }
        uploaded = upload_source(volumes["/source"], source)
        print(f"Source cache: uploaded {uploaded} new content blobs", flush=True)
        app = modal.App(run["app_name"], include_source=False)
        function = app.function(
            image=build_image(config, source),
            gpu=spec["gpu_request"],
            cpu=run["cpu"],
            memory=run["memory_mib"],
            timeout=run["timeout_seconds"] + run["shutdown_grace_seconds"] + 120,
            startup_timeout=run["startup_timeout_seconds"],
            volumes=volumes,
            secrets=[
                modal.Secret.from_name(name, environment_name=environment)
                for name in settings["secrets"]
            ],
            max_containers=1,
            scaledown_window=2,
            retries=0,
            include_source=False,
        )(execute)
        result = None
        with (
            modal.enable_output(show_progress=False),
            app.run(environment_name=environment),
            modal.Queue.ephemeral(environment_name=environment) as channel,
            StopRequests() as stops,
        ):
            call = function.spawn(spec, channel)
            atomic_write(
                destination / "call.json",
                [json.dumps({"call_id": call.object_id}, indent=2).encode() + b"\n"],
            )
            finished = False
            try:
                result = wait_for_result(call, channel, spec, stops)
                finished = True
            finally:
                if not finished:
                    with contextlib.suppress(Exception):
                        call.cancel(terminate_containers=True)
                    atomic_write(
                        destination / "interrupted.json",
                        [
                            json.dumps(
                                {"status": "interrupted", "call_id": call.object_id}
                            ).encode()
                            + b"\n"
                        ],
                    )
                    print(
                        f"Emergency stop. Recover with --download {spec['run_id']} using the same launcher and configuration.",
                        flush=True,
                    )
        if result is None:
            raise KeyboardInterrupt
        return result
