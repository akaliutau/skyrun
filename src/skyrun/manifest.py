"""Deterministic, bounded source snapshots with a project-local ignore policy."""

import hashlib
import json
import os
import stat
import zipfile
from dataclasses import dataclass
from importlib.resources import files
from io import BytesIO
from pathlib import Path, PurePosixPath

import pathspec

from skyrun.config import Config
from skyrun.paths import RESERVED, checked_root, inside

CHUNK_SIZE = 1024 * 1024


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def selected(path, selections):
    path = PurePosixPath(path)
    return any(path == PurePosixPath(s) or PurePosixPath(s) in path.parents for s in selections)


def structural_exclusion(relative):
    parts = PurePosixPath(relative).parts
    return ".git" in parts or parts[0] in RESERVED


class IgnorePolicy:
    def __init__(self, patterns):
        self.patterns = list(patterns)
        self.spec = pathspec.GitIgnoreSpec.from_lines(self.patterns)

    @classmethod
    def load(cls, project, name=".modalignore"):
        baseline = files("skyrun").joinpath("default.modalignore").read_text(encoding="utf-8")
        path = inside(project, name, allow_hidden=True)
        patterns = baseline.splitlines()
        if path.is_file():
            patterns.extend(path.read_text(encoding="utf-8").splitlines())
        return cls(patterns)

    def excludes(self, relative, *, directory=False):
        name = str(relative) + ("/" if directory else "")
        return structural_exclusion(relative) or self.spec.match_file(name)


def walk_files(project, policy, *, excluded=(), selections=()):
    """Walk regular files only. Parent directory exclusions follow gitignore semantics."""
    project = checked_root(project)
    excluded = tuple(PurePosixPath(p) for p in excluded)

    def skip(path, directory=False):
        relative = PurePosixPath(path.relative_to(project).as_posix())
        if (
            selections
            and not selected(relative, selections)
            and (not directory or not any(relative in PurePosixPath(p).parents for p in selections))
        ):
            return True
        return (
            path.is_symlink()
            or any(relative == p or p in relative.parents for p in excluded)
            or policy.excludes(relative, directory=directory)
        )

    def onerror(error):
        raise error

    for current, directories, names in os.walk(project, followlinks=False, onerror=onerror):
        directories[:] = sorted(d for d in directories if not skip(Path(current) / d, True))
        for name in sorted(names):
            path = Path(current) / name
            if not skip(path) and path.is_file():
                yield path, path.relative_to(project).as_posix()


def freeze_file(source, target, remaining):
    """Hash exactly the frozen bytes; refuse special files or a changing source."""
    checked_root(source)
    before = source.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"not a regular source file: {source}")
    if before.st_size > remaining:
        raise ValueError("source exceeds sync upload budget; keep large weights in /cache")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    # O_NOFOLLOW prevents replacing the selected file with a symlink during scanning.
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as incoming, target.open("wb") as outgoing:
        opened = os.fstat(incoming.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"source changed while snapshotting: {source}; retry")
        while chunk := incoming.read(CHUNK_SIZE):
            size += len(chunk)
            if size > remaining:
                raise ValueError("source grew beyond the sync upload budget while snapshotting")
            digest.update(chunk)
            outgoing.write(chunk)
        after = os.fstat(incoming.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError(f"source changed while snapshotting: {source}; retry")
    mode = 0o755 if before.st_mode & 0o111 else 0o644
    target.chmod(mode)
    return {"bytes": size, "sha256": digest.hexdigest(), "mode": mode}


@dataclass(frozen=True)
class Snapshot:
    directory: Path
    manifest: dict
    ignore_patterns: tuple[str, ...]
    sync_baseline: dict
    requirements: Path | None = None

    @property
    def project(self):
        return self.directory / "project"

    def archive(self):
        """A deterministic in-memory bundle for integrations that prefer an archive."""
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in self.manifest["files"]:
                info = zipfile.ZipInfo(entry["path"], date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | entry["mode"]) << 16
                with (
                    (self.project / entry["path"]).open("rb") as incoming,
                    archive.open(info, "w") as outgoing,
                ):
                    while chunk := incoming.read(CHUNK_SIZE):
                        outgoing.write(chunk)
        return stream.getvalue()


def snapshot(config: Config, destination, *, exclude_roots=()) -> Snapshot:
    destination = checked_root(destination)
    destination.mkdir(parents=True, exist_ok=True)
    project = config.project
    if ".git" in project.parts:
        raise ValueError("project_dir cannot select a Git metadata directory")
    if not project.is_dir():
        raise ValueError(f"project directory does not exist: {project}")
    policy = IgnorePolicy.load(project, config["sync"]["ignore_file"])
    excluded = list(config["sync"]["output_dirs"])
    local = inside(config.root, config["sync"]["local_dir"], allow_hidden=True)
    if local.is_relative_to(project):
        relative = local.relative_to(project)
        if not relative.parts:
            raise ValueError("sync.local_dir cannot be the project directory")
        excluded.append(relative.as_posix())
    if destination.is_relative_to(project):
        excluded.append(destination.relative_to(project).as_posix())
    for extra in exclude_roots:
        extra = checked_root(extra)
        if extra == project:
            raise ValueError("output directory cannot be the project directory")
        if extra.is_relative_to(project):
            excluded.append(extra.relative_to(project).as_posix())
    budget = config["sync"]["max_upload_mib"] * 1024 * 1024
    count_limit = config["sync"]["max_upload_files"]
    entries, total, baseline = [], 0, {}
    for source, relative in walk_files(project, policy, excluded=excluded):
        if len(entries) >= count_limit:
            raise ValueError("source exceeds sync.max_upload_files")
        target = inside(destination / "project", relative, allow_hidden=True)
        metadata = freeze_file(source, target, budget - total)
        total += metadata["bytes"]
        entry = {"path": relative, **metadata}
        entries.append(entry)
        if selected(relative, config["sync"]["bidirectional"]):
            baseline[relative] = metadata
    if not entries:
        raise ValueError("project contains no uploadable files")
    entries.sort(key=lambda e: e["path"])
    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {"schema_version": 1, "sha256": digest, "bytes": total, "files": entries}
    requirements = None
    if config["image"]["requirements"] is not None:
        source = inside(config.root, config["image"]["requirements"], allow_hidden=True)
        requirements = destination / "image" / "requirements.txt"
        freeze_file(source, requirements, budget)
        # Modal installs this exact file, independently of the project source tree.
        for line in requirements.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(
                ("-r", "--requirement", "-c", "--constraint", "-e", "--editable", ".", "/")
            ):
                raise ValueError(
                    "image.requirements must be self-contained; use registry packages or URLs, without local includes"
                )
    return Snapshot(destination, manifest, tuple(policy.patterns), baseline, requirements)
