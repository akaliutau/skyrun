# Quick start

Skyrun uploads a frozen copy of one project folder, executes an ordinary command
on Modal, streams its output, and downloads saved files. Your scripts use normal
Python and have no dependency on Skyrun or Modal.

## Install from this repository

Use Python 3.11 or newer in a virtual environment:

```bash
git clone https://github.com/akaliutau/skyrun.git
cd skyrun
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[modal]'
python -m modal setup
```

On Windows, activate with `.venv\Scripts\activate`. If your environment is
already authenticated with Modal, keep using those credentials. Skyrun does not
copy local credentials or forward local environment variables.

After a release has been published to PyPI, install it with
`python -m pip install 'skyrun[modal]'`. Until then, use the repository install
above. The core `skyrun` installation supports initialization and dry runs without
the Modal SDK.

## Run the CPU hello example

Every wheel includes the sample projects. Create one in an empty directory:

```bash
skyrun init /tmp/skyrun-hello --example hello
cd /tmp/skyrun-hello
skyrun --dry-run
skyrun run
```

The first command copies `main.py`, `skyrun.toml`, and `.modalignore`. The dry run
prints every source path, size, and SHA-256 hash without contacting Modal. The
actual run requests CPU compute and prints `Hello from skyrun` as it executes.
Modal compute and persistent storage are billed to your Modal account.

The terminal prints a run ID and its artifact directory. Read
`runs/<run-id>/outputs/hello.json` for the saved result and
`runs/<run-id>/_runner/console.log` for the complete output. The example also runs
directly with `python main.py` on your own machine.

## Run an existing project folder

Initialize configuration in the folder that contains your code:

```bash
cd /path/to/my-project
skyrun init
skyrun --dry-run -- python train.py --epochs 5
skyrun run --gpu L4 --timeout 1800 -- python train.py --epochs 5
```

Plain `skyrun init` writes configuration and ignore rules without adding a script.
It refuses to overwrite existing files. Configure dependencies in the generated
`skyrun.toml`, for example:

```toml
[image]
requirements = "requirements-gpu.txt"
apt_packages = ["git", "ffmpeg"]
```

Requirements paths are relative to the configuration file. Keep that file
self-contained; use registry dependencies or direct URLs rather than local
requirements includes or editable installs. Packages installed only on your local
machine are not installed remotely.

You can keep configuration outside your project and select its folder explicitly:

```bash
skyrun run --config /path/to/skyrun.toml --project-dir /path/to/my-project -- python train.py
```

The command runs with that project as its working directory. Arguments after
`--` form an argv list and are passed directly to the subprocess. Shell expansion,
pipelines, and interactive stdin are not provided. To run a shell script, include
it in the project and use `-- bash script.sh`.

## Try training or inference

```bash
skyrun init /tmp/skyrun-training --example training
cd /tmp/skyrun-training
skyrun run
```

The PyTorch example trains a small linear model, displays a live progress bar,
and saves a model/optimizer checkpoint. After `TRAINING_READY`, press Ctrl-C once
to save and stop. Look for `checkpoints/last.pt` and `outputs/training.json` under
the local run directory. To try it on remote CPU:

```bash
skyrun run --gpu cpu -- python train.py --steps 20 --delay 0
```

Create the inference example with `skyrun init /tmp/skyrun-inference --example
inference`. It reports the allocated hardware and runs the pinned public
Qwen2.5 0.5B Instruct checkpoint. The first run installs packages and downloads
weights; later runs reuse the image and model cache. See the
[example catalog](examples.md) for complete commands and expected outputs.

## Bring selected files back into your project

Output directories always download into the run directory. For files you want to
round-trip into your working project, opt in using `[sync]`:

```toml
[sync]
project_dir = "."
output_dirs = ["outputs", "checkpoints"]
bidirectional = ["settings.json", "data/processed"]
```

Existing selected files upload with the source; new and modified selected files
are copied back after the run, including handled failures and stops. Concurrent
local edits cause a conflict, leaving both versions available. Sync happens at
run boundaries. See [file synchronization](synchronization.md) for ignore rules,
conflict resolution, and checkpoint-resume configuration.

## Recover a run

```bash
skyrun download RUN_ID
skyrun download RUN_ID --output-dir /tmp/recovered
```

Recovery uses committed files on the configured artifact Volume and does not
allocate compute. Keep the original volume name and Modal environment. Recovery
downloads files by default; add `--sync` to apply selected changes to the project.
See [configuration and operations](configuration.md) for controls and limits.
