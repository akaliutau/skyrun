# Configuration and operations

## Discovery and paths

Skyrun searches the current directory and its parents. At each level it tries
`skyrun.toml`, then `[tool.skyrun]` in `pyproject.toml`. Use `--config PATH` to
choose explicitly. An unrelated
`pyproject.toml` is skipped. Invalid configuration fails locally before provider
access. Unknown keys are errors, helping catch misspelled settings.

Paths are relative to the configuration directory, except that ignore rules,
output paths, and bidirectional selections are relative to the workload project.
`sync.project_dir` also accepts an explicit absolute folder. `--project-dir`
selects a folder relative to the local current directory. `--output-dir` accepts
an absolute or relative local run parent without changing configuration.

## Dedicated configuration

```toml
provider = "modal"

[run]
app_name = "my-training"
gpu = "T4"                     # cpu, L4, A100-80GB, H100:2, ...
cpu = 1
memory_mib = 4096
timeout_seconds = 600
startup_timeout_seconds = 600
shutdown_grace_seconds = 60

[modal]
cache_volume = "my-training-cache"
artifact_volume = "my-training-artifacts"
source_volume = "my-training-source"
# environment_name = "dev"    # otherwise use the active Modal environment
secrets = []                    # Modal Secret names, never local secret values

[image]
python_version = "3.11"
apt_packages = ["git"]
requirements = "requirements-gpu.txt"
pip_packages = []
# base_image = "nvidia/cuda:12.8.1-devel-ubuntu22.04"

[environment]
TOKENIZERS_PARALLELISM = "false"
OMP_NUM_THREADS = "1"

[sync]
project_dir = "src"
output_dirs = ["outputs", "checkpoints"]
local_dir = "runs"
ignore_file = ".modalignore"
max_upload_files = 1024
max_upload_mib = 64
bidirectional = []

[workload]
terminal = true
command = ["python", "train.py"]
```

Everything except the project command can usually keep its default. If
`image.requirements` is omitted, no workload requirements file is needed. Omitted
Volume names derive from `run.app_name`: `-cache`, `-artifacts`, and `-source`.
Local runs download under `runs/` by default. The default command is
`["python", "main.py"]`; `skyrun init` creates CPU-oriented configuration, while
resource defaults request one T4, one CPU, and 4 GiB of host RAM.

For `pyproject.toml`, prefix each table with `tool.skyrun`, for example:

```toml
[tool.skyrun]
provider = "modal"

[tool.skyrun.run]
gpu = "L4"

[tool.skyrun.sync]
project_dir = "src"

[tool.skyrun.workload]
command = ["python", "train.py"]
```

## Resources and images

GPU strings/counts pass through to Modal. `cpu` or `none` requests no GPU.
CPU must be positive; memory and timeouts must be positive integers. The workload
timeout plus shutdown allowance plus 120 seconds for wrapper finalization must
fit within Modal's 24-hour function limit. Shutdown allowance is 1–3,600 seconds.

Dependencies come from a frozen, self-contained requirements file and optional
`image.pip_packages`. Use `apt_packages` for operating-system libraries. A custom
`base_image` can provide a CUDA development toolchain for extensions needing
`nvcc`; pin a compatible Python/package/CUDA combination. Regular PyTorch wheels
include runtime libraries and may not include the compiler toolchain. Modal
caches the resulting image; see its [image guide](https://modal.com/docs/guide/images)
and [GPU guide](https://modal.com/docs/guide/gpu) for supported combinations.

Skyrun deliberately uses `retries=0`: a failed training command must not execute a
second time unexpectedly. A source cache lookup or artifact recovery does not
allocate compute.

## Environment and secrets

`[environment]` contains string values only. These apply to the workload and its
children, override cache-path defaults, and are saved in run metadata. They do not
configure image build steps or your local shell. Local environment variables are
not automatically forwarded.

For private values, create a Modal Secret and configure its name in
`[modal].secrets`. Skyrun stores the names, not the secret values, in configuration.
Your program can still expose secrets by printing them. Inspect specific variables
with `skyrun --gpu cpu -- printenv MY_SETTING`; a full `printenv` includes every
variable available to the workload and is retained in the console log.

## Streaming and stopping

The default PTY makes normal `tqdm` bars see a terminal, preserving carriage
returns, Unicode, and cursor controls. Terminal dimensions come from the local
terminal at launch. Set `workload.terminal = false` to use pipes. Output streams
without waiting for newlines, subject to network latency. Skyrun does not forward
interactive stdin or notebook widgets.

The first Ctrl-C or SIGTERM sends SIGINT to the workload process group and allows
`shutdown_grace_seconds` for cleanup. A second Ctrl-C requests a forced kill while
the wrapper remains alive to commit and download files. A third is an emergency
container cancellation. Cancellation while queued prevents the workload from
starting. Timeouts use the same graceful cleanup allowance.

The complete raw output goes to `_runner/console.log`. If the viewer falls behind,
live-view bytes can be dropped; `stream_dropped_bytes` records the loss. The
persisted log keeps those bytes. Redirected launcher output contains no added
color escapes; workload-authored terminal controls remain workload output.

## Run records and exit codes

```text
runs/<run-id>/
  submitted.json           Frozen source, argv, resource budget and settings
  call.json                Provider function-call ID
  result.json              Local copy of execution status
  sync.json                Optional sync application report
  _runner/console.log      Full combined stdout/stderr
  _runner/source.json      Source manifest and hashes
  _runner/result.json      Remote execution record
  _sync/manifest.json      Optional selected-file changes and launch baseline
  _sync/files/...          New/modified selected files
  outputs/...              Configured outputs
  checkpoints/...
```

Normal completion preserves the command's exit code; signal exits follow
`128 + signal`. User cancellation returns `130`; timeout returns `124`.
CLI configuration/usage errors return `2`; sync conflicts return `3`; other
launcher/transport errors return `1`. A workload can itself return these numbers;
use the saved status to distinguish causes. `workload_returncode` records the
actual subprocess exit, alongside `started`, `forced`, and elapsed time.

Artifact downloads use streamed, unique temporary files and atomic replacements.
An interrupted transfer leaves an existing local file intact. Retry with
`skyrun download RUN_ID`; matching committed artifact files are replaced within
that run only. Recovering selected changes into the working project additionally
requires `--sync`.
