# Examples

Examples are distributed as package resources and copied into ordinary folders by
`skyrun init --example NAME`. They work after installing a wheel, without cloning
the repository. Their Python scripts also run directly with a compatible local
environment. Each example has its own application and Volume names.

| Example | Command to create | Default compute | Dependencies | Demonstrates |
| --- | --- | --- | --- | --- |
| [Hello](../src/skyrun/examples/hello) | `skyrun init hello --example hello` | CPU | Standard library | Upload, live logs, JSON artifact |
| [Training](../src/skyrun/examples/training) | `skyrun init training --example training` | T4 | Pinned PyTorch, NumPy, tqdm | Terminal progress, graceful stop, checkpoints |
| [Inference](../src/skyrun/examples/inference) | `skyrun init inference --example inference` | T4 | Pinned PyTorch and Transformers stack | Hardware report, model cache, GPU inference |

## Hello

```bash
skyrun init hello --example hello
cd hello
skyrun --dry-run
skyrun run
```

Expected: `Hello from skyrun`, exit code `0`, and
`runs/<run-id>/outputs/hello.json`. No model or training libraries are installed.

## Training

```bash
skyrun init training --example training
cd training
skyrun run
```

After `TRAINING_READY`, Ctrl-C sends SIGINT to the complete workload process
group. The sample catches `KeyboardInterrupt`, saves from `finally`, and produces
`checkpoints/last.pt` with model state, optimizer state, and the completed step.
The launcher returns `130` for a user stop, even when cleanup exits successfully.

For a short CPU run and a larger multi-GPU request:

```bash
skyrun run --gpu cpu -- python train.py --steps 20 --delay 0
# Use your own distributed training script for torchrun:
skyrun run --gpu H100:2 --timeout 7200 -- torchrun --standalone --nnodes 1 --nproc-per-node 2 my_train.py
```

The included toy trainer uses one device; `my_train.py` is your own script.
Checkpoint creation belongs to the workload. Skyrun cannot infer how to serialize
an arbitrary model or optimizer. The supplied example saves on completion and
interruption; it does not automatically reload an earlier checkpoint. A resume
script should load the selected checkpoint itself, using the
[round-trip checkpoint configuration](synchronization.md#resumable-checkpoints).

## Inference

```bash
skyrun init inference --example inference
cd inference
skyrun run
skyrun run --gpu L4 --timeout 900 -- python demo.py --require-cuda --max-new-tokens 32
```

Expected artifacts:

```text
outputs/environment.json   OS, Python, installed packages, CPU/RAM and GPU details
outputs/inference.json     Model revision, actual device/dtype and inference timing
outputs/generated.txt      Generated text
```

The script pins `Qwen/Qwen2.5-0.5B-Instruct` to revision
`7ae557604adf67be50417f59c2c2f167def9a775`, uses safetensors, and disables remote
code. It requests CUDA by default, generates at most 48 tokens, and has a
600-second workload budget. Model files persist under `/cache`; they are not
bundled into Skyrun or downloaded into your run artifacts. The checkpoint's
[model card and license](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/7ae557604adf67be50417f59c2c2f167def9a775)
are separate from Skyrun's MIT license.

To run this sample on remote CPU, remove the script's CUDA requirement explicitly:

```bash
skyrun run --gpu cpu -- python demo.py
```
