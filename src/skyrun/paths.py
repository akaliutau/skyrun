"""Shared path checks; this module uses only the Python standard library."""

import os
import re
import tempfile
from pathlib import Path, PurePosixPath

PROJECT_LOCK_NAME = ".skyrun.lock"
RESERVED = {"_runner", "_sync", PROJECT_LOCK_NAME}


def relative_path(value, *, allow_hidden=False):
    """Validate a portable relative path without normalizing away unsafe parts."""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"expected a relative POSIX path: {value!r}")
    parts = value.split("/")
    if any(
        not p or p in {".", ".."} or ":" in p or (p.startswith(".") and not allow_hidden)
        for p in parts
    ):
        raise ValueError(f"unsafe relative path: {value!r}")
    return PurePosixPath(value)


def checked_root(root):
    """Reject symlinks in an absolute root, including its existing parents."""
    path = Path(os.path.abspath(root))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"symlinks are excluded: {component}")
    return path


def inside(root, relative, *, allow_hidden=False):
    path = checked_root(root)
    for part in relative_path(str(relative), allow_hidden=allow_hidden).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlinks are excluded: {path}")
    return path


def output_paths(values, *, allow_empty=False, allow_hidden=False):
    if not isinstance(values, (list, tuple)) or (not values and not allow_empty):
        raise ValueError("sync.output_dirs must be a list of directory paths")
    paths = [relative_path(value, allow_hidden=allow_hidden) for value in values]
    for index, path in enumerate(paths):
        if path.parts[0] in RESERVED or any(
            part.casefold() == PROJECT_LOCK_NAME for part in path.parts
        ):
            raise ValueError("_runner, _sync, and .skyrun.lock are reserved for Skyrun metadata")
        for other in paths[:index]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError("selected paths must be distinct and cannot overlap")
    return paths


def validate_run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise ValueError("invalid run ID")
    return value


def atomic_write(target, chunks, *, before_replace=None):
    """Stream bytes to a unique sibling temporary file and atomically replace."""
    target = Path(target)
    checked_root(target.parent)
    if target.is_symlink():
        raise ValueError(f"symlink destination: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".skyrun-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            for chunk in chunks:
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if target.is_symlink():
            raise ValueError(f"symlink destination: {target}")
        if before_replace is not None:
            before_replace()
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
