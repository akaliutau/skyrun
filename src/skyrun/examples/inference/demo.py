"""Ordinary Python + PyTorch. Runs unchanged on Modal, a workstation or another cloud."""

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def command_info(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"unavailable": str(error)}


def file_info(path):
    try:
        return Path(path).read_text().strip()
    except OSError as error:
        return f"unavailable: {error}"


def environment_info():
    packages = sorted(
        (
            {"name": dist.metadata["Name"], "version": dist.version}
            for dist in importlib.metadata.distributions()
        ),
        key=lambda item: item["name"].lower(),
    )
    gpu_info = []
    for index in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(index)
        free, total = torch.cuda.mem_get_info(index)
        gpu_info.append(
            {
                "index": index,
                "name": props.name,
                "vram_bytes": props.total_memory,
                "free_bytes": free,
                "total_bytes": total,
                "compute_capability": list(torch.cuda.get_device_capability(index)),
                "multiprocessors": props.multi_processor_count,
            }
        )
    usage = shutil.disk_usage(Path.cwd())
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "cpu": command_info(["lscpu"]),
        "memory": command_info(["free", "-h"]),
        "linux_memory": file_info("/proc/meminfo"),
        "container_limits": {
            "cpu_quota": file_info("/sys/fs/cgroup/cpu.max"),
            "memory_bytes": file_info("/sys/fs/cgroup/memory.max"),
        },
        "cwd": str(Path.cwd()),
        "workspace_disk_bytes": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        },
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "cuda_available": torch.cuda.is_available(),
        "gpu_count": len(gpu_info),
        "gpus": gpu_info,
        "nvidia_smi": command_info(["nvidia-smi"]),
        "gpu_topology": command_info(["nvidia-smi", "topo", "-m"]),
        "packages": packages,
        # Selected paths only; never dump environment variables or credentials.
        "cache_paths": {
            key: os.environ.get(key) for key in ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--prompt", default="Explain in two sentences what a GPU does.")
    parser.add_argument("--max-new-tokens", type=int, default=48)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_new_tokens <= 512:
        parser.error("max-new-tokens must be from 1 to 512")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    info = environment_info()
    print(json.dumps(info, indent=2), flush=True)
    (args.output_dir / "environment.json").write_text(json.dumps(info, indent=2) + "\n")
    if args.require_cuda and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable; inspect environment.json")
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    torch.manual_seed(0)
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False
    )
    model = (
        AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            revision=MODEL_REVISION,
            torch_dtype=dtype,
            use_safetensors=True,
            trust_remote_code=False,
        )
        .to(device)
        .eval()
    )
    messages = [{"role": "user", "content": args.prompt}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    if device.type == "cuda":
        torch.cuda.synchronize()
    loaded = time.monotonic()
    with torch.inference_mode():
        tokens = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False)
    if device.type == "cuda":
        torch.cuda.synchronize()
    generated = tokens[0, inputs["input_ids"].shape[1] :]
    text = tokenizer.decode(generated, skip_special_tokens=True)
    result = {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "license": "Apache-2.0",
        "device": str(next(model.parameters()).device),
        "dtype": str(next(model.parameters()).dtype),
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "prompt": args.prompt,
        "text": text,
        "generated_tokens": len(generated),
        "max_new_tokens": args.max_new_tokens,
        "load_seconds": round(loaded - started, 3),
        "inference_seconds": round(time.monotonic() - loaded, 3),
        "peak_gpu_bytes": torch.cuda.max_memory_allocated() if device.type == "cuda" else None,
    }
    print(json.dumps(result, indent=2), flush=True)
    (args.output_dir / "inference.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.output_dir / "generated.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
