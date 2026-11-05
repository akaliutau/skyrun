# Portable Python on Modal GPUs

Write normal Python in `src/`. Run one local command to upload a frozen source
snapshot, execute it on the requested GPU, stream logs, and download selected
output directories. The code in `src/` has no Modal imports, decorators or
runner dependencies. This folder works after extraction anywhere; it does not
import anything from the research repository that produced it.

You need Python 3.11+ locally, internet access, and an authenticated Modal account.
Use your existing Python environment with Modal installed and authorized; the
launcher reads the SDK's normal local credentials. Credentials are never copied
into the archive or uploaded source. If you need to install the launcher SDK:

```bash
conda create -n modal1 python=3.12 -y
conda activate modal1
pip install -r requirements.txt
# Only for a machine not already authorized:
python -m modal setup
```

Run the included example from this folder:

```bash
python run.py
```

Terminal output uses light grey, including Modal's remote logs. Redirected output
uses plain text without added color codes.

It prints the operating system, Python and installed packages, CPU and host RAM,
CUDA/cuDNN, `nvidia-smi`, GPU count, names, VRAM, compute capabilities and topology.
It then loads a pinned, public Qwen2.5 0.5B Instruct checkpoint and generates text
on `cuda:0` in float16. The default command requires CUDA and fails if GPU access
is missing. Its 48-token output cap and 600-second workload deadline keep the
smoke bounded. Environment reporting selects hardware/software details and cache
paths; it does not print credentials or all environment variables.

The first run builds a Python 3.11 image, installs `requirements-gpu.txt`, and
downloads the model. Later runs reuse the image and model cache. The archive
contains source/configuration, not Python packages or model weights: a first run
requires downloads. See [VALIDATION.md](VALIDATION.md) for measured checks.

The workload's stdout and stderr stream directly to your local terminal while it
runs. A remote pseudo-terminal is enabled by default, so ordinary CLI `tqdm`
bars see a terminal and update in place, including carriage returns, Unicode and
cursor movements for nested bars. Output is not held until a newline or until
the job finishes. The terminal width is taken from your local terminal at launch.
Updates still incur network/relay latency; this is live output, not a local GPU.

Try the included small PyTorch training example to see progress and stop it:

```bash
python run.py -- python train_demo.py --require-cuda --steps 600 --delay 0.2
# The same code also works on remote CPU:
python run.py --gpu cpu -- python train_demo.py --steps 600 --delay 0.2
```

After `TRAINING_READY`, press **Ctrl-C once**. The client stays connected, sends
`SIGINT` to the remote workload's process group and keeps streaming its cleanup
output. Python code can catch `KeyboardInterrupt` or save from `finally`, exactly
as it would locally. The wrapper then commits the output files and logs, downloads
them automatically and exits with code 130. The sample saves model/optimizer
state to `checkpoints/last.pt` and a summary to `outputs/training.json`.

Your own training code must implement its checkpoint save operation; the runner
cannot infer how to serialize an arbitrary model or optimizer. Existing saved
files are retained even when the script has no cleanup handler. A portable pattern
is:

```python
try:
    train()
except KeyboardInterrupt:
    print("Stopping training...")
finally:
    save_checkpoint("checkpoints/last.pt")
```

The default cleanup allowance is 60 seconds. Change
`shutdown_grace_seconds = 120` in the existing `[modal]` section of `gpu.toml`
when your checkpoint needs longer. If the allowance expires, the wrapper kills
the remaining workload processes and still commits/downloads their saved files.
Pressing **Ctrl-C a second time** skips the remaining allowance and forces the
workload to stop, while leaving the wrapper alive to retrieve files. A third
press is an emergency container cancellation and cannot guarantee a fresh commit.
If the workload is still queued, Ctrl-C cancels it without starting the training.

Timeouts use the same cleanup allowance and return 124. A timeout can therefore
take up to its configured workload budget plus the cleanup allowance. The run
record distinguishes `canceled`, `timed_out`, the actual workload exit code, and
whether a forced kill was needed.

The stream carries terminal output; it does not forward interactive stdin or
notebook widgets. Set `terminal = false` in `[workload]` for programs that should
see pipes instead of a terminal. Full raw output is saved in `_runner/console.log`
even if a slow/disconnected viewer falls behind; `stream_dropped_bytes` records
any live-view bytes omitted due to backpressure. Files download at the end/stop,
rather than continuously mirroring every checkpoint to the local machine.

The launcher prints a run ID and creates this local layout:

```text
runs/<run-id>/
  submitted.json                 command, hardware budget and source hashes
  call.json                      Modal function call ID
  result.json                    status, exit code and timing
  _runner/console.log            full combined stdout/stderr
  _runner/result.json            remote run record
  _runner/source.json            exact uploaded manifest and hashes
  outputs/environment.json       example's hardware and software report
  outputs/inference.json         model revision, actual device/dtype and inference
  outputs/generated.txt          generated response
  checkpoints/                   created remotely; downloaded when it has files
```

Each run gets fresh output directories. Empty directories are not downloaded.
Model weights stay on `/cache` in a persistent Modal Volume; run outputs live in
`/artifacts/runs/<run-id>/` on a different persistent Volume. The remote worker
links the configured relative output directories to that artifact location, so
your program uses ordinary paths such as `outputs/` and `checkpoints/`.

Change the GPU, budget, arguments or local output destination without editing
your workload:

```bash
python run.py --gpu L4 --timeout 900 -- python demo.py --require-cuda --max-new-tokens 32
python run.py --output-dir /tmp/my-gpu-runs
python run.py --dry-run
```

The default development request is one T4, one CPU and 4 GiB of host RAM.
Increase CPU and host RAM explicitly for your own training workload.
`gpu.toml` configures the src folder, command, GPU, CPU, RAM, timeouts, image
Python version, OS packages, volume names and output directories. Launcher
arguments go before `--`; the ordinary subprocess command and its arguments go
after it. Arguments are passed directly without a shell. The working directory
is the uploaded project's root, and sibling modules are importable normally.

To add remote Python packages, append them to `requirements-gpu.txt`, keeping
the existing dependencies. For example, add `pandas==2.2.3`, or the versions of
PEFT and datasets required by your training script. `requirements.txt` is for
the local launcher only. Next time you run `python run.py`, Modal automatically
builds the updated image and caches it for subsequent runs. Packages installed
only in your local environment are not copied to the remote machine.

For Linux packages, edit the existing `[image]` table in `gpu.toml`:

```toml
[image]
python_version = "3.11"
apt_packages = ["git", "curl", "ffmpeg", "build-essential"]
```

The launcher installs these through apt when building the remote image. You do
not need to run apt manually inside your workload.

Set remote workload environment variables in the `[environment]` table:

```toml
[environment]
TOKENIZERS_PARALLELISM = "false"
MY_SETTING = "example value"
OMP_NUM_THREADS = "1"
```

All values must be strings, including numbers and booleans. These settings apply
to the workload and its subprocesses, take precedence over the runner's cache
defaults, and are saved in the run metadata. They do not set variables on your
local machine or during image/package installation. Local environment variables
are not automatically forwarded. Your unchanged Python code reads them normally:

```python
import os

print(os.environ["MY_SETTING"])
```

Inspect the actual remote environment with the standard `printenv` command:

```bash
python run.py --gpu cpu -- printenv             # complete CPU workload environment
python run.py --gpu cpu -- printenv MY_SETTING  # one variable
python run.py -- printenv                      # include the allocated GPU's variables
```

These commands replace the demo for that run, so they do not load the model.
Their stdout is also downloaded as `_runner/console.log`. A full `printenv`
includes any secret values present in that process; use named variables when
you want to inspect only specific settings.

To fine-tune a model, add your own `src/train.py` and training data under
`src/`, add dependencies such as PEFT or datasets to `requirements-gpu.txt`,
and direct its saved adapters/checkpoints to a configured output directory:

```bash
python run.py --gpu A100-80GB --timeout 7200 -- python train.py --output_dir checkpoints
# Example for a training script that supports PyTorch distributed execution:
python run.py --gpu H100:2 --timeout 7200 -- torchrun --standalone --nnodes 1 --nproc-per-node 2 train.py
```

These commands assume you have written `train.py`; the included sample performs
inference. GPU strings/counts are passed through to Modal. T4 is the tested
default; other GPUs need compatible PyTorch/CUDA versions and suitable dtypes.
The default pip wheels provide CUDA runtime libraries, not the `nvcc` development
toolchain. For code compiling custom CUDA extensions, adapt the image definition
in `run.py` to a suitable CUDA development image and pin its dependencies.

Run exactly the same example directly on another machine, without the launcher:

```bash
python -m pip install -r requirements-gpu.txt
cd src
python demo.py --require-cuda
# The same script also supports CPU execution:
python demo.py
```

Your own scripts can use `torch.cuda`, Hugging Face Trainer, Accelerate or
`torchrun` as usual. Hardware selection and file transport belong to the launcher.
For a remote CPU run of the sample, override its CUDA requirement:

```bash
python run.py --gpu cpu -- python demo.py
```

Python errors, graceful stops and handled command timeouts still commit and
download saved outputs and logs. Normal completion preserves the workload exit
code; a user stop returns 130 and a timeout returns 124. Child training workers
are included in the process group cleanup.
Downloads stream to disk, including large checkpoints, instead of returning file
contents through a function response. If a download or client session is
interrupted, recover committed files without allocating a GPU:

```bash
python run.py --download RUN_ID
python run.py --download RUN_ID --output-dir /tmp/recovered
```

Recovery uses the same Modal environment and artifact volume named in `gpu.toml`.
It replaces matching files within that run only. A hard container kill, preemption
or network loss can leave only the last committed files. This launcher is
synchronous; losing the client connection is not a graceful-stop request. It is
not a detached job scheduler or
a continuous bidirectional filesystem mirror. Source edits take effect next run.

`sync_manifest.py` is the only upload policy. It scans the configured named project
folder, never the starter/repository root. It excludes hidden files/directories,
symlinks, environments, caches, saved weights, solution files and configured
outputs, with a default limit of 1,024 files / 64 MiB. The three explicitly listed
runtime/dependency files are also included. Inspect every uploaded path and hash
with `--dry-run`. Keep large downloadable weights in the remote cache; increase
the explicit limits if your project needs larger input data. Modify this policy
when your workload needs otherwise excluded file types. Plain non-hidden files
inside `src/` are considered inputs, so keep private material elsewhere.

Use separate volume names in `gpu.toml` when starting an unrelated project.
Persistent volumes remain after a run; manage retention with Modal's volume tools.
The starter uses public weights and does not forward local secrets or require a
Hugging Face token. Model and tokenizer downloads pin revision
`7ae557604adf67be50417f59c2c2f167def9a775`, use safetensors, and disable remote code.

Run focused CPU checks or rebuild the distributable:

```bash
python -m unittest -v checks
python build_archive.py --output modal-gpu-starter.zip
```

References: [Modal GPU selection](https://modal.com/docs/guide/gpu),
[Modal image and package configuration](https://modal.com/docs/guide/images),
[Modal Python app lifecycle and logs](https://modal.com/docs/guide/apps),
[Modal control and streaming queues](https://modal.com/docs/guide/queues),
[tqdm terminal behavior](https://tqdm.github.io/docs/tqdm/),
[Modal Volumes](https://modal.com/docs/guide/volumes),
[Qwen model and Apache 2.0 license](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/7ae557604adf67be50417f59c2c2f167def9a775).
