"""Command line interface for project initialization, execution, and artifact recovery."""

import argparse
import contextlib
import json
import re
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from skyrun import __version__
from skyrun.config import load_config
from skyrun.errors import ConfigError, SyncConflict
from skyrun.manifest import snapshot
from skyrun.paths import atomic_write, checked_root
from skyrun.runner import download, make_spec, run


class LightGreyStream:
    """Keep terminal logs readable while preserving cursor controls and redirected bytes."""

    def __init__(self, stream):
        self.stream = stream
        self.pending_escape = ""

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, text):
        length = len(text)
        text = self.pending_escape + text
        self.pending_escape = ""
        incomplete = re.search(r"\x1b(?:\[[0-?]*[ -/]*)?$", text)
        if incomplete:
            self.pending_escape = incomplete.group()
            text = text[: incomplete.start()]
        if text:
            plain = re.sub(r"\x1b\[[0-9;:]*m", "", text)
            self.stream.write("\x1b[37m" + plain + "\x1b[0m")
        return length


def init_project(destination, *, example=None):
    """Copy a packaged example, checking all collisions before writing any file."""
    destination = checked_root(destination)
    resource = files("skyrun.examples").joinpath(example or "hello")
    names = ["skyrun.toml", ".modalignore"]
    if example:
        names.extend(
            name.name
            for name in resource.iterdir()
            if name.name not in {*names, "__init__.py", "__pycache__"} and name.is_file()
        )
    payloads = {name: resource.joinpath(name).read_bytes() for name in names}
    for name in payloads:
        path = destination / name
        if path.exists() or path.is_symlink():
            raise ConfigError(
                f"refusing to overwrite {path}; choose an empty directory or initialize manually"
            )
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in payloads.items():
        atomic_write(destination / name, [content])
    print(f"Created {example or 'configuration'} in {destination}")
    print(f"Inspect uploads: skyrun --config {destination / 'skyrun.toml'} --dry-run")
    return destination


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "init":
        parser = argparse.ArgumentParser(
            prog="skyrun init",
            description="Initialize configuration or copy a runnable, packaged example.",
        )
        parser.add_argument("directory", type=Path, nargs="?", default=Path.cwd())
        parser.add_argument("--example", choices=("hello", "training", "inference"))
        args = parser.parse_args(argv[1:])
        init_project(args.directory, example=args.example)
        return 0
    if argv and argv[0] == "run":
        argv.pop(0)
    elif argv and argv[0] == "download":
        argv[0] = "--download"
    parser = argparse.ArgumentParser(
        prog="skyrun",
        description="Run ordinary project code on cloud compute. Subcommands: init, run, download.",
        epilog="Example: skyrun run --gpu L4 -- python train.py",
    )
    parser.add_argument("--version", action="version", version=f"skyrun {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        help="skyrun.toml or pyproject.toml (discovered by default)",
    )
    parser.add_argument("--provider", help="provider adapter name (default: modal)")
    parser.add_argument(
        "--project-dir",
        type=Path,
        help="explicit project folder to upload (relative to current directory)",
    )
    parser.add_argument("--gpu", help="GPU type/count, e.g. T4 or H100:2; cpu for no GPU")
    parser.add_argument("--timeout", type=int, help="workload budget in seconds")
    parser.add_argument("--output-dir", type=Path, help="local parent directory for run artifacts")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print exact frozen upload manifest without contacting a provider",
    )
    parser.add_argument(
        "--download", metavar="RUN_ID", help="recover committed files without allocating compute"
    )
    parser.add_argument(
        "--sync", action="store_true", help="also apply selected files when recovering a run"
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="download changes into run artifacts without applying them to the project",
    )
    parser.add_argument(
        "--sync-conflict",
        choices=("error", "local", "remote"),
        default="error",
        help="handle concurrent local edits (default: error; preserves both versions)",
    )
    parser.add_argument("--debug", action="store_true", help="show Python tracebacks for failures")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="argv to execute, after --")
    args = parser.parse_args(argv)
    if args.dry_run and args.download:
        parser.error("--dry-run and --download cannot be combined")
    if args.sync and args.no_sync:
        parser.error("--sync and --no-sync cannot be combined")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    config = load_config(args.config).override(
        gpu=args.gpu,
        timeout=args.timeout,
        command=command if args.command else None,
        provider=args.provider,
        project_dir=args.project_dir,
    )
    if args.download:
        if args.command:
            parser.error("a download does not accept a workload command")
        download(
            args.download,
            config,
            output_dir=args.output_dir,
            sync=args.sync,
            conflict=args.sync_conflict,
        )
        return 0
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="skyrun-dry-run-") as directory:
            extra = [args.output_dir] if args.output_dir is not None else []
            source = snapshot(config, directory, exclude_roots=extra)
            spec = make_spec(config, source, "dry-run")
            print(json.dumps(spec, indent=2))
        return 0
    return run(
        config, output_dir=args.output_dir, sync=not args.no_sync, conflict=args.sync_conflict
    ).returncode


def entrypoint(argv=None):
    output = LightGreyStream(sys.stdout) if sys.stdout.isatty() else sys.stdout
    errors = LightGreyStream(sys.stderr) if sys.stderr.isatty() else sys.stderr
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        try:
            code = main(argv)
        except KeyboardInterrupt:
            code = 130
        except Exception as error:
            if "--debug" in sys.argv:
                raise
            print(f"skyrun: {error}", file=sys.stderr)
            code = (
                3
                if isinstance(error, SyncConflict)
                else 2
                if isinstance(error, (ConfigError, ValueError))
                else 1
            )
    raise SystemExit(code)
