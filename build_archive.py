"""Build the distributable using an explicit allowlist, with no repository traversal."""

import argparse
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = (
    ".gitignore",
    "README.md",
    "VALIDATION.md",
    "gpu.toml",
    "requirements.txt",
    "requirements-gpu.txt",
    "run.py",
    "local_session.py",
    "sync_manifest.py",
    "modal_runner.py",
    "project/demo.py",
    "project/train_demo.py",
    "checks.py",
    "build_archive.py",
)


def build(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(FILES):
            source = ROOT / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"expected a regular distribution file: {name}")
            info = zipfile.ZipInfo(
                "modal-gpu-starter/" + name, date_time=(2026, 1, 1, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    print(f"{destination.resolve()} ({destination.stat().st_size:,} bytes)")
    print(f"sha256 {hashlib.sha256(destination.read_bytes()).hexdigest()}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("modal-gpu-starter.zip"))
    build(parser.parse_args().output)
