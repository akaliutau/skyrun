# Skyrun

**Run your projects on cloud compute.**

[![CI](https://github.com/akaliutau/skyrun/actions/workflows/ci.yml/badge.svg)](https://github.com/akaliutau/skyrun/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/akaliutau/skyrun/blob/main/LICENSE)

Skyrun allows to smoothly execute your project on Modal CPU or
GPU compute, streams live terminal output, and downloads saved files. Optional
bidirectional sync brings selected changes back into your working project. 
Your scripts need no Modal imports or runner decorators.

The package exposes a CLI and Python API, with a provider interface for future
backends. 

NOTE: this is a community version and supports a limited number if providers (Modal is the first supported provider since 0.1.0).
Enterprise version supports all neoclouds, AWS/GCP/Azure and custom infra. 

[Quick start](https://github.com/akaliutau/skyrun/blob/main/docs/quickstart.md) · [Examples](https://github.com/akaliutau/skyrun/blob/main/docs/examples.md) ·
[Configuration](https://github.com/akaliutau/skyrun/blob/main/docs/configuration.md) · [File sync](https://github.com/akaliutau/skyrun/blob/main/docs/synchronization.md) ·
[Python API & architecture](https://github.com/akaliutau/skyrun/blob/main/docs/architecture.md) · [Publishing](https://github.com/akaliutau/skyrun/blob/main/docs/publishing.md)

## Why Skyrun

- **Run anywhere philosophy.** Run the same scripts locally or remotely using ordinary
  argv commands, sibling imports, and filesystem paths.
- **Flexible sourcing.** Only the chosen project folder is scanned; 
  `.modalignore` controls eligible files, just like `.gitignore`
- **Lazy and efficient file transfers.** Immutable source files are reused by content hash.
  Artifact downloads stream to disk with atomic file replacement.
- **Remote [machine] is here experience.** Live PTY output supports normal tqdm progress bars,
  carriage returns, Unicode, and cursor movement. Full logs remain available.
- **Graceful stops.** Ctrl-C signals the workload process group, allows checkpoint
  cleanup, then commits and retrieves saved files. Repeated requests escalate.
- **Selective round trips.** Explicitly chosen files or directories return to the
  working project with integrity checks and conflict detection.
- **Extensible infrastructure - run anywhere.** Provider SDKs stay behind adapters. Core config,
  snapshots, examples, and dry runs work without cloud authentication.

## Installation I

```bash
pip install 'skyrun[modal]'
```

## Installation II (from source code)

Use Python 3.11+ and an authenticated Modal account. From this repository:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[modal]'
python -m modal setup  # only if this environment is not already authenticated
```

```bash
conda create -n skyrun python=3.12 -y
conda activate skyrun
pip install -e '.[modal]'
python -m modal setup  # only if this environment is not already authenticated
```

Generating a sample config, so one can update it for your case:

```bash
skyrun init /tmp/skyrun-hello --example hello
cd /tmp/skyrun-hello
skyrun --dry-run
skyrun run
```

## Quick start

The hello example uses CPU and the Python standard library. Expect
`Hello from skyrun`, exit `0`, and `runs/<run-id>/outputs/hello.json`. Actual runs
allocate resources in your Modal account; initialization and dry runs are offline.

For an existing project, run `skyrun init` in its folder, configure its remote
requirements, inspect uploads, and invoke its normal command:

```bash
skyrun run --gpu T4 --timeout 1800 -- python train.py --epochs 5
# Select a folder explicitly using configuration stored elsewhere:
skyrun run --config /path/to/skyrun.toml --project-dir /path/to/project -- python train.py
```

Arguments after `--` go directly to the subprocess. The command's working directory
is the uploaded project root. The [complete quick start](https://github.com/akaliutau/skyrun/blob/main/docs/quickstart.md)
explains installation, dependencies, saved files, and recovery.

## Examples included with every installation

| Example | Create a runnable project | Default compute | What it shows |
| --- | --- | --- | --- |
| Hello | `skyrun init hello --example hello` | CPU | Minimal command, live logs, saved JSON |
| Training | `skyrun init training --example training` | T4 | PyTorch, tqdm, Ctrl-C checkpoint save |
| Inference | `skyrun init inference --example inference` | T4 | Hardware report, pinned Qwen model, model cache |

Then enter the created directory and run `skyrun --dry-run` followed by
`skyrun run`. Examples include their configuration, ignore file, and any required
remote dependencies. They are package resources, so users of a wheel get the same
samples as repository users. See [commands and expected outputs](https://github.com/akaliutau/skyrun/blob/main/docs/examples.md).

## Configuration

Use `skyrun.toml` or `[tool.skyrun]` in `pyproject.toml`. Runtime resources belong
under `[run]`; provider settings belong under `[modal]`. A typical configuration is:

```toml
provider = "modal"

[run]
app_name = "my-project"
gpu = "T4"
timeout_seconds = 1800
shutdown_grace_seconds = 60

[image]
python_version = "3.11"
requirements = "requirements-gpu.txt"
apt_packages = ["git"]

[sync]
project_dir = "src"
output_dirs = ["outputs", "checkpoints"]
local_dir = "runs"

[workload]
command = ["python", "train.py"]
terminal = true
```

Relative paths are based on the configuration directory; output paths and ignore
rules are based on the project folder. Unspecified Volume names derive from the
application name. Local environments and credentials are not copied. Configure
workload variables in `[environment]` and private values through named Modal
Secrets. See the [configuration reference](https://github.com/akaliutau/skyrun/blob/main/docs/configuration.md).

## Optional bidirectional sync

Output directories always download into the run directory. To bring selected
files back into your original working project, add an explicit selection:

```toml
[sync]
project_dir = "src"
output_dirs = ["outputs", "checkpoints"]
bidirectional = ["settings.json", "data/processed"]
```

Selected files upload at launch. New and modified selected files return after
execution, including handled failures and stops. Local changes made during the
run cause a conflict: local files stay intact and remote versions remain under
`runs/<run-id>/_sync/files/`. Use `--no-sync` to defer application or recover with
an explicit conflict policy:

```bash
skyrun download RUN_ID --sync --sync-conflict local
skyrun download RUN_ID --sync --sync-conflict remote
```

Sync runs at launch/completion boundaries. Deletions are reported, never propagated
automatically. Selections are literal paths, obey `.modalignore`, and must not
overlap output directories. See [sync rules and resumable checkpoints](https://github.com/akaliutau/skyrun/blob/main/docs/synchronization.md).

## Logs, interruption, and recovery

Each invocation creates `runs/<run-id>/` with the submitted manifest, execution
status, complete `_runner/console.log`, and configured output files. Model caches
persist on a separate Volume. Source blobs use their own content-addressed Volume.

Press Ctrl-C once to request SIGINT and allow workload cleanup. A second press
forces the workload to stop while the wrapper retrieves saved files. A third is
an emergency cancellation. Your code must save its own checkpoint, for example
from `finally`; the runner cannot serialize an arbitrary model automatically.

Normal exits preserve the command's exit code. Cancellation returns `130` and
timeout returns `124`. A timeout includes the configured cleanup allowance.
Recover committed files without allocating a GPU:

```bash
skyrun download RUN_ID
skyrun download RUN_ID --output-dir /tmp/recovered
```

The client is synchronous. Losing its connection is not a graceful stop request;
a hard kill or preemption may leave only previously committed files. The output
stream supports terminal output and progress bars; it does not forward interactive
stdin. See [operations and exit codes](https://github.com/akaliutau/skyrun/blob/main/docs/configuration.md).

## Architecture

```
                              LOCAL MACHINE                                              MODAL CONTAINER
 ┌──────────────────────────────────────────────────────────────────┐        ┌────────────────────────────────────────┐
 │  CLI / SDK Entrypoint ($ skyrun --gpu A100 -- python train.py)   │        │   Remote Runner Engine                 │
 │  ─────────────────────────────────────────────────────────────   │        │   ───────────────────────────────────  │
 │  1. Parse Config (`pyproject.toml` or `skyrun.toml`)             │        │   1. Mount Artifact Volume             │
 │  2. Scan & Hash Source                                           │        │   2. Extract Source Bundle             │
 │  3. Pack In-Memory Zip/Tar Archive                               │        │   3. Set Up Process Group + PTY        │
 │  4. Invoke Remote Modal App                                      │ ────>  │   4. Execute Subprocess (eg.`train.py`)│
 │                                                                  │        │   5. Catch Signals / Harvest Artifacts │
 │  5. Stream Live Logs / Terminal Output (PTY)                     │ <────  │   6. Save to `/artifacts/<run_id>/`    │
 │  6. Download Artifacts to `runs/<run_id>/`                       │        │                                        │
 └──────────────────────────────────────────────────────────────────┘        └────────────────────────────────────────┘

```


## Python API

```python
from skyrun import download, run

result = run("skyrun.toml", gpu="L4", command=["python", "train.py"])
print(result.run_id, result.status, result.returncode, result.directory)
download(result.run_id, "skyrun.toml", output_dir="/tmp/recovered")
```

The provider contract supports third-party adapters through the `skyrun.providers`
entry-point group. See [architecture and extension points](https://github.com/akaliutau/skyrun/blob/main/docs/architecture.md).

## Development and releases

```bash
python -m pip install -e '.[modal,dev]'
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
python -m twine check --strict dist/*
python tools/check_dist.py
```

Offline tests exercise real subprocess lifecycle behavior, source policy,
transport interruption, recovery, and sync conflicts. Live Modal tests require
explicit opt-in. Read [validation](https://github.com/akaliutau/skyrun/blob/main/docs/validation.md) for the test boundaries and
GPU release checks.

The [publishing guide](https://github.com/akaliutau/skyrun/blob/main/docs/publishing.md) covers namespace ownership, building
and checking distributions, clean-wheel example validation, TestPyPI rehearsal,
Trusted Publishing, manual uploads, release tags, and post-release verification.
The repository includes CI and separate TestPyPI/PyPI publish jobs.

Contributions follow [CONTRIBUTING.md](https://github.com/akaliutau/skyrun/blob/main/CONTRIBUTING.md) and the
[code of conduct](https://github.com/akaliutau/skyrun/blob/main/CODE_OF_CONDUCT.md). See [AUTHORS.md](https://github.com/akaliutau/skyrun/blob/main/AUTHORS.md),
[security reporting](https://github.com/akaliutau/skyrun/blob/main/SECURITY.md), and the [changelog](https://github.com/akaliutau/skyrun/blob/main/CHANGELOG.md).
Skyrun is licensed under [MIT](https://github.com/akaliutau/skyrun/blob/main/LICENSE).
