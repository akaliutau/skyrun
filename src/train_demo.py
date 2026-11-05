"""Portable toy training with live tqdm and a checkpoint saved on normal exit/Ctrl-C."""

import argparse
import json
import time
from pathlib import Path

import torch
from tqdm import trange


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    if args.steps < 1 or args.delay < 0:
        parser.error("steps must be positive and delay cannot be negative")
    if args.require_cuda and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(0)
    model = torch.nn.Linear(16, 1).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    inputs = torch.randn(64, 16, device=device)
    targets = inputs.sum(dim=1, keepdim=True)
    checkpoint_dir, output_dir = Path("checkpoints"), Path("outputs")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    step, loss_value = 0, None
    print(f"TRAINING_READY device={device}; press Ctrl-C to save and stop", flush=True)
    try:
        with trange(
            args.steps, desc="Training", dynamic_ncols=True, mininterval=0.1
        ) as progress:
            for _ in progress:
                optimizer.zero_grad(set_to_none=True)
                loss = torch.nn.functional.mse_loss(model(inputs), targets)
                loss.backward()
                optimizer.step()
                step += 1
                loss_value = loss.item()
                progress.set_postfix(loss=f"{loss_value:.4f}")
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nTraining interrupted; saving checkpoint...", flush=True)
    finally:
        temporary = checkpoint_dir / "last.pt.tmp"
        torch.save(
            {
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "step": step,
            },
            temporary,
        )
        temporary.replace(checkpoint_dir / "last.pt")
        summary = {
            "step": step,
            "requested_steps": args.steps,
            "device": str(device),
            "dtype": str(next(model.parameters()).dtype),
            "loss": loss_value,
            "stopped_early": step < args.steps,
        }
        (output_dir / "training.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"CHECKPOINT_SAVED checkpoints/last.pt at step {step}", flush=True)


if __name__ == "__main__":
    main()
