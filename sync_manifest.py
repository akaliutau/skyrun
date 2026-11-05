"""The sole source upload policy. Scan one named project folder, never its parent."""

import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath

RUNTIME_FILES = ("modal_runner.py", "sync_manifest.py", "requirements-gpu.txt")
SKIP_DIRS = {
    "__pycache__",
    "venv",
    "env",
    "site-packages",
    "__pypackages__",
    "node_modules",
    "build",
    "dist",
    "cache",
    "models",
    "weights",
    "checkpoints",
    "artifacts",
    "runs",
}
SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".pt",
    ".pth",
    ".ckpt",
    ".safetensors",
    ".bin",
    ".pem",
    ".key",
}


def relative_path(value, *, allow_hidden=False):
    """A non-hidden, strictly relative POSIX path with no parent traversal."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"expected a relative POSIX path: {value!r}")
    parts = value.split("/")
    if any(
        not p or p in {".", ".."} or (p.startswith(".") and not allow_hidden)
        for p in parts
    ):
        raise ValueError(f"unsafe relative path: {value!r}")
    return PurePosixPath(value)


def inside(root, relative, *, allow_hidden=False):
    """Reject symlinks at every component, including a selected directory itself."""
    path = Path(root)
    for part in relative_path(str(relative), allow_hidden=allow_hidden).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlinks are excluded: {path}")
    return path


def output_paths(values):
    if not isinstance(values, list) or not values:
        raise ValueError("sync.output_dirs must be a nonempty list")
    paths = [relative_path(value) for value in values]
    for index, path in enumerate(paths):
        if path.parts[0] == "_runner":
            raise ValueError("_runner is reserved for logs and run metadata")
        for other in paths[:index]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError(
                    "output directories must be distinct and cannot overlap"
                )
    return paths


def upload_files(root, config):
    """Yield (local path, snapshot path), pruning outputs, caches and secrets."""
    root = Path(root).resolve()
    if any(
        part in SKIP_DIRS for part in relative_path(config["sync"]["project_dir"]).parts
    ):
        raise ValueError(
            "project_dir cannot select an environment, cache or output tree"
        )
    project = inside(root, config["sync"]["project_dir"])
    outputs = output_paths(config["sync"]["output_dirs"])
    if not project.is_dir():
        raise ValueError(f"project directory does not exist: {project}")
    for name in RUNTIME_FILES:
        path = inside(root, name)
        if not path.is_file():
            raise ValueError(f"missing runtime file: {path}")
        yield path, PurePosixPath(name)

    def excluded(path):
        relative = PurePosixPath(path.relative_to(project).as_posix())
        return (
            path.is_symlink()
            or any(p.startswith(".") or p in SKIP_DIRS for p in relative.parts)
            or any(relative == out or out in relative.parents for out in outputs)
            or "solutions" in path.name.lower()
            or path.suffix.lower() in SKIP_SUFFIXES
        )

    # Do not replace this with project.parent.rglob() or add_local_dir(root).
    for current, directories, names in os.walk(project, followlinks=False):
        directories[:] = sorted(
            d for d in directories if not excluded(Path(current) / d)
        )
        for name in sorted(names):
            path = Path(current) / name
            if not excluded(path) and path.is_file():
                yield (
                    path,
                    PurePosixPath("project") / path.relative_to(project).as_posix(),
                )


def snapshot(root, config, destination):
    """Freeze exactly what will be uploaded; fingerprint the frozen bytes."""
    destination = Path(destination)
    limit = config["sync"]["max_upload_mib"] * 1024 * 1024
    max_files = config["sync"]["max_upload_files"]
    if limit <= 0 or max_files <= 0:
        raise ValueError("upload budgets must be positive")
    files, total, digest = [], 0, hashlib.sha256()
    for source, relative in sorted(
        upload_files(root, config), key=lambda item: str(item[1])
    ):
        if len(files) >= max_files or total + source.stat().st_size > limit:
            raise ValueError(
                "source exceeds sync upload budget; keep weights in /cache"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        total += target.stat().st_size
        if total > limit:
            raise ValueError(
                "source grew beyond the sync upload budget while snapshotting"
            )
        content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        digest.update(str(relative).encode() + b"\0" + bytes.fromhex(content_hash))
        files.append(
            {
                "path": str(relative),
                "bytes": target.stat().st_size,
                "sha256": content_hash,
            }
        )
    if not any(item["path"].startswith("project/") for item in files):
        raise ValueError("project contains no uploadable files")
    return {"sha256": digest.hexdigest(), "bytes": total, "files": files}
