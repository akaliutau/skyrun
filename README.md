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
python -m pip install -r requirements.txt
# Only for a machine not already authorized:
python -m modal setup
```

Run the included example from this folder:

```bash
python run.py
```

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
is the uploaded src's root, and sibling modules are importable normally.

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

Python errors and handled command timeouts still commit and download saved
outputs and logs. The launcher exits with the workload's exit code (`124` for
its timeout), and terminates the process group to clean up training workers.
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
synchronous; disconnecting cancels the run. It is not a detached job scheduler or
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
[Modal Python app lifecycle and logs](https://modal.com/docs/guide/apps),
[Modal Volumes](https://modal.com/docs/guide/volumes),
[Qwen model and Apache 2.0 license](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/7ae557604adf67be50417f59c2c2f167def9a775).
