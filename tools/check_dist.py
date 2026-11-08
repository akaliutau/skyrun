"""Inspect built artifacts and test an installed wheel, without cloud access."""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


def command(argv, *, cwd, env):
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"distribution check failed: {argv[0]} (exit {result.returncode})")
    return result.stdout


def check(directory):
    wheels, sources = sorted(directory.glob("*.whl")), sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise SystemExit(
            "expected exactly one wheel and one sdist in dist/; use an empty directory"
        )
    required = {
        "skyrun/py.typed",
        "skyrun/default.modalignore",
        "skyrun/remote/app.py",
        "skyrun/examples/hello/main.py",
        "skyrun/examples/hello/skyrun.toml",
        "skyrun/examples/hello/.modalignore",
        "skyrun/examples/training/train.py",
        "skyrun/examples/training/requirements-gpu.txt",
        "skyrun/examples/training/.modalignore",
        "skyrun/examples/inference/demo.py",
        "skyrun/examples/inference/skyrun.toml",
        "skyrun/examples/inference/.modalignore",
    }
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        if missing := required - names:
            raise SystemExit(f"wheel is missing required resources: {sorted(missing)}")
        entry_points = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        if "skyrun = skyrun.cli:entrypoint" not in wheel.read(entry_points).decode():
            raise SystemExit("wheel has no skyrun console entry point")
    with tarfile.open(sources[0]) as source:
        source_names = {name.split("/", 1)[-1] for name in source.getnames()}
        for name in source_names:
            parts = Path(name).parts
            if name.endswith(".py") and (
                len(parts) == 1 or (len(parts) == 2 and parts[0] == "src")
            ):
                raise SystemExit(f"sdist contains a standalone script outside the package: {name}")
        for expected in (
            "CONTRIBUTING.md",
            "docs/quickstart.md",
            "docs/publishing.md",
            "tests/test_engine.py",
            "tools/check_dist.py",
            "src/skyrun/config.py",
            "src/skyrun/examples/inference/requirements-gpu.txt",
        ):
            if expected not in source_names:
                raise SystemExit(f"sdist is missing {expected}")
        if any(
            "runs" in Path(name).parts
            or "plan.txt" in Path(name).parts
            or ".env" in Path(name).parts
            for name in source_names
        ):
            raise SystemExit("sdist contains generated or private state")
    with tempfile.TemporaryDirectory(prefix="skyrun-wheel-check-") as temporary:
        root = Path(temporary).resolve()
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        console = environment / ("Scripts/skyrun.exe" if os.name == "nt" else "bin/skyrun")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        command([str(python), "-m", "pip", "install", str(wheels[0].resolve())], cwd=root, env=env)
        command(
            [
                str(python),
                "-c",
                "import importlib.util; assert importlib.util.find_spec('modal') is None",
            ],
            cwd=root,
            env=env,
        )
        command(
            [
                str(python),
                "-c",
                "from skyrun import RunResult, download, load_config, run; import sys; assert callable(run) and 'modal' not in sys.modules",
            ],
            cwd=root,
            env=env,
        )
        print(command([str(python), "-m", "skyrun", "--version"], cwd=root, env=env).strip())
        assert console.is_file(), "console script was not installed"
        command([str(console), "--version"], cwd=root, env=env)
        for example in ("hello", "training", "inference"):
            project = root / example
            command(
                [str(python), "-m", "skyrun", "init", str(project), "--example", example],
                cwd=root,
                env=env,
            )
            raw = command(
                [
                    str(python),
                    "-m",
                    "skyrun",
                    "--config",
                    str(project / "skyrun.toml"),
                    "--dry-run",
                ],
                cwd=root,
                env=env,
            )
            spec = json.loads(raw)
            assert spec["source"]["files"], f"empty {example} source"
            print(f"Installed example: {example} ({len(spec['source']['files'])} source files)")
        command([str(python), "main.py"], cwd=root / "hello", env=env)
        assert (root / "hello/outputs/hello.json").is_file()
    print("Wheel, sdist, offline CLI, all examples, and local hello check passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    check(parser.parse_args().dist.absolute())
