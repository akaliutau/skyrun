"""Run-boundary, opt-in bidirectional sync without implicit overwrites or deletes."""

import json
import os
import re
from pathlib import Path

from skyrun.errors import SyncConflict
from skyrun.manifest import (
    IgnorePolicy,
    file_hash,
    freeze_file,
    selected,
    structural_exclusion,
    walk_files,
)
from skyrun.paths import atomic_write, checked_root, inside, output_paths, relative_path


def collect_sync(spec, workspace, artifacts):
    selections = spec.get("bidirectional", [])
    if not selections:
        return
    baseline = spec.get("sync_baseline", {})
    policy = IgnorePolicy(spec["ignore_patterns"])
    changed, seen = [], set()
    for source, relative in walk_files(
        workspace, policy, excluded=spec["output_dirs"], selections=selections
    ):
        if not selected(relative, selections):
            continue
        seen.add(relative)
        original = baseline.get(relative)
        mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
        if original and file_hash(source) == original["sha256"] and mode == original["mode"]:
            continue
        target = inside(artifacts, "_sync/files/" + relative, allow_hidden=True)
        metadata = freeze_file(source, target, source.stat().st_size)
        changed.append({"path": relative, **metadata})
    manifest = {
        "schema_version": 1,
        "selected": selections,
        "baseline": baseline,
        "files": changed,
        "deleted": sorted(set(baseline) - seen),
    }
    target = inside(artifacts, "_sync/manifest.json", allow_hidden=True)
    atomic_write(target, [json.dumps(manifest, indent=2).encode() + b"\n"])


def _metadata(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
        or type(value.get("bytes")) is not int
        or value["bytes"] < 0
        or value.get("mode") not in {0o644, 0o755}
    ):
        raise ValueError("invalid sync file metadata")


def _local_version(target):
    if not target.exists():
        return None
    if not target.is_file() or target.is_symlink():
        raise ValueError(f"not a regular sync destination: {target}")
    return {"sha256": file_hash(target), "mode": 0o755 if target.stat().st_mode & 0o111 else 0o644}


def _path_in_selection(relative, selections):
    relative_path(relative, allow_hidden=True)
    if structural_exclusion(relative) or not selected(relative, selections):
        raise ValueError(f"unselected or reserved sync path: {relative}")


def apply_sync(project, run_directory, *, conflict="error", selections=None):
    """Apply changed files only. On conflict, preserve every local file and the baseline."""
    if conflict not in {"error", "local", "remote"}:
        raise ValueError("sync conflict policy must be error, local, or remote")
    run_directory = Path(run_directory)
    manifest_path = inside(run_directory, "_sync/manifest.json", allow_hidden=True)
    if not manifest_path.is_file():
        return {"applied": [], "conflicts": [], "deleted_remote": []}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("unsupported sync manifest version")
    if (
        not isinstance(manifest.get("selected"), list)
        or not isinstance(manifest.get("baseline"), dict)
        or not isinstance(manifest.get("files"), list)
        or not isinstance(manifest.get("deleted"), list)
    ):
        raise ValueError("invalid sync manifest")
    output_paths(manifest["selected"], allow_empty=True, allow_hidden=True)
    if selections is not None and manifest["selected"] != list(selections):
        raise ValueError("sync selection differs from the submitted configuration")
    for relative, metadata in manifest["baseline"].items():
        _path_in_selection(relative, manifest["selected"])
        _metadata(metadata)
    for relative in manifest["deleted"]:
        _path_in_selection(relative, manifest["selected"])
    project = checked_root(project)
    plan, conflicts, seen, blocked = [], [], set(), []
    for entry in manifest["files"]:
        _metadata(entry)
        relative = entry.get("path")
        _path_in_selection(relative, manifest["selected"])
        if relative in seen:
            raise ValueError(f"duplicate or unselected sync path: {relative}")
        seen.add(relative)
        source = inside(run_directory, "_sync/files/" + relative, allow_hidden=True)
        target = inside(project, relative, allow_hidden=True)
        if source.stat().st_size != entry["bytes"] or file_hash(source) != entry["sha256"]:
            raise ValueError(f"sync artifact failed integrity check: {relative}")
        parents = [p for p in target.parents if p.is_relative_to(project)]
        if (target.exists() and not target.is_file()) or any(
            p.exists() and not p.is_dir() for p in parents
        ):
            conflicts.append(relative)
            blocked.append(relative)
            continue
        current = _local_version(target)
        original = manifest["baseline"].get(relative)
        current_hash = current["sha256"] if current else None
        unchanged = current_hash == (original["sha256"] if original else None)
        unchanged = unchanged and (
            not current or not original or current["mode"] == original["mode"]
        )
        already_applied = (
            current and current["sha256"] == entry["sha256"] and current["mode"] == entry["mode"]
        )
        if already_applied:
            continue
        if not unchanged:
            conflicts.append(relative)
            if conflict != "remote":
                continue
        plan.append((source, target, entry, current))
    report = {"applied": [], "conflicts": conflicts, "deleted_remote": manifest["deleted"]}
    if conflicts and (conflict == "error" or (blocked and conflict == "remote")):
        atomic_write(run_directory / "sync.json", [json.dumps(report, indent=2).encode() + b"\n"])
        raise SyncConflict(conflicts)
    try:
        for source, target, entry, expected in plan:
            # This check runs after copying the temporary file, just before replacement.
            def check_current(target=target, expected=expected, relative=entry["path"]):
                inside(project, relative, allow_hidden=True)
                if _local_version(target) != expected:
                    report["conflicts"].append(relative)
                    raise SyncConflict([relative])

            with source.open("rb") as incoming:
                atomic_write(
                    target,
                    iter(lambda: incoming.read(1024 * 1024), b""),
                    before_replace=check_current,
                )
            os.chmod(target, entry["mode"])
            report["applied"].append(entry["path"])
    finally:
        atomic_write(run_directory / "sync.json", [json.dumps(report, indent=2).encode() + b"\n"])
    return report
