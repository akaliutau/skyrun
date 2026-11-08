"""Configuration discovery, defaults, and strict local validation."""

import copy
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from skyrun.errors import ConfigError
from skyrun.paths import RESERVED, checked_root, inside, output_paths, relative_path

DEFAULTS = {
    "provider": "modal",
    "provider_options": {},
    "run": {
        "app_name": "skyrun",
        "gpu": "T4",
        "cpu": 1,
        "memory_mib": 4096,
        "timeout_seconds": 600,
        "startup_timeout_seconds": 600,
        "shutdown_grace_seconds": 60,
    },
    "modal": {
        "cache_volume": "skyrun-cache",
        "artifact_volume": "skyrun-artifacts",
        "source_volume": "skyrun-source",
        "environment_name": None,
        "secrets": [],
    },
    "image": {
        "python_version": "3.11",
        "apt_packages": ["git"],
        "requirements": None,
        "pip_packages": [],
        "base_image": None,
    },
    "environment": {},
    "sync": {
        "project_dir": ".",
        "output_dirs": ["outputs", "checkpoints"],
        "local_dir": "runs",
        "max_upload_files": 1024,
        "max_upload_mib": 64,
        "ignore_file": ".modalignore",
        "bidirectional": [],
    },
    "workload": {"terminal": True, "command": ["python", "main.py"]},
}


@dataclass(frozen=True)
class Config:
    root: Path
    data: dict
    path: Path | None = None

    def __getitem__(self, key):
        return self.data[key]

    @property
    def project(self):
        name = self.data["sync"]["project_dir"]
        if Path(name).is_absolute():
            return checked_root(name)
        return self.root if name == "." else inside(self.root, name, allow_hidden=True)

    def override(self, *, gpu=None, timeout=None, command=None, provider=None, project_dir=None):
        data = copy.deepcopy(self.data)
        if gpu is not None:
            data["run"]["gpu"] = gpu
        if timeout is not None:
            data["run"]["timeout_seconds"] = timeout
        if command is not None:
            data["workload"]["command"] = list(command)
        if provider is not None:
            data["provider"] = provider
        if project_dir is not None:
            data["sync"]["project_dir"] = str(checked_root(project_dir))
        validate(data)
        return Config(self.root, data, self.path)


def _normalize(raw):
    if not isinstance(raw, dict):
        raise ConfigError("skyrun configuration must be a TOML table")
    raw = copy.deepcopy(raw)
    unknown = raw.keys() - DEFAULTS.keys()
    if unknown:
        raise ConfigError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    data = copy.deepcopy(DEFAULTS)
    for section, value in raw.items():
        if isinstance(DEFAULTS[section], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{section} must be a table")
            extra = (
                value.keys() - DEFAULTS[section].keys()
                if section not in {"environment", "provider_options"}
                else set()
            )
            if extra:
                raise ConfigError(f"unknown {section} keys: {', '.join(sorted(extra))}")
            data[section].update(value)
        else:
            data[section] = value
    for name, suffix in (
        ("source_volume", "source"),
        ("cache_volume", "cache"),
        ("artifact_volume", "artifacts"),
    ):
        if name not in raw.get("modal", {}):
            data["modal"][name] = str(data["run"]["app_name"]) + "-" + suffix
    validate(data)
    return data


def _positive_int(value, name, maximum=None):
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        suffix = f" from 1 to {maximum}" if maximum else " greater than zero"
        raise ConfigError(f"{name} must be an integer{suffix}")


def _strings(value, name, *, nonempty=False):
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or not item or "\x00" in item for item in value)
    ):
        raise ConfigError(f"{name} must be a {'nonempty ' if nonempty else ''}list of strings")


def validate(data):
    run, sync, image = data["run"], data["sync"], data["image"]
    if not isinstance(data["provider"], str) or not re.fullmatch(
        r"[a-z][a-z0-9_-]*", data["provider"]
    ):
        raise ConfigError("provider must be a lowercase provider name")
    for name in ("app_name", "gpu"):
        if not isinstance(run[name], str) or not run[name] or "\x00" in run[name]:
            raise ConfigError(f"run.{name} must be a nonempty string")
    cpu = run["cpu"]
    if (
        isinstance(cpu, bool)
        or not isinstance(cpu, (int, float))
        or not math.isfinite(cpu)
        or cpu <= 0
    ):
        raise ConfigError("run.cpu must be a positive finite number")
    for name in ("memory_mib", "timeout_seconds", "startup_timeout_seconds"):
        _positive_int(run[name], f"run.{name}")
    _positive_int(run["shutdown_grace_seconds"], "run.shutdown_grace_seconds", 3600)
    if run["timeout_seconds"] + run["shutdown_grace_seconds"] + 120 > 86400:
        raise ConfigError(
            "workload timeout + shutdown grace + 120 seconds must fit within 24 hours"
        )
    _positive_int(sync["max_upload_files"], "sync.max_upload_files")
    _positive_int(sync["max_upload_mib"], "sync.max_upload_mib")
    try:
        if not isinstance(sync["project_dir"], str) or not sync["project_dir"]:
            raise ConfigError("sync.project_dir must be a directory path")
        if Path(sync["project_dir"]).is_absolute():
            checked_root(sync["project_dir"])
        elif sync["project_dir"] != ".":
            relative_path(sync["project_dir"], allow_hidden=True)
        relative_path(sync["local_dir"], allow_hidden=True)
        relative_path(sync["ignore_file"], allow_hidden=True)
        outputs = output_paths(sync["output_dirs"], allow_empty=True)
        selections = output_paths(sync["bidirectional"], allow_empty=True, allow_hidden=True)
        if any(".git" in p.parts for p in selections):
            raise ConfigError("Git metadata cannot be synchronized")
        if any(any(c in str(p) for c in "*?[]") for p in selections):
            raise ConfigError(
                "sync.bidirectional accepts literal file or directory paths, without globs"
            )
        for selected in selections:
            for output in outputs:
                if selected == output or selected in output.parents or output in selected.parents:
                    raise ConfigError("bidirectional paths and output_dirs cannot overlap")
        if any(p.parts[0] in RESERVED for p in selections):
            raise ConfigError("runner metadata cannot be synchronized")
    except (ValueError, TypeError) as error:
        raise ConfigError(str(error)) from error
    environment = data["environment"]
    if not isinstance(environment, dict) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        or not isinstance(value, str)
        or "\x00" in value
        for key, value in environment.items()
    ):
        raise ConfigError("environment must contain valid variable names and string values")
    _strings(data["workload"]["command"], "workload.command", nonempty=True)
    if not isinstance(data["workload"]["terminal"], bool):
        raise ConfigError("workload.terminal must be true or false")
    _strings(image["apt_packages"], "image.apt_packages")
    _strings(image["pip_packages"], "image.pip_packages")
    if not isinstance(image["python_version"], str) or not re.fullmatch(
        r"3\.(?:11|12|13|14)", image["python_version"]
    ):
        raise ConfigError("image.python_version must be 3.11, 3.12, 3.13, or 3.14")
    for key in ("base_image", "requirements"):
        value = image[key]
        if value is not None and (not isinstance(value, str) or not value or "\x00" in value):
            raise ConfigError(f"image.{key} must be a nonempty string")
    if image["requirements"] is not None:
        try:
            relative_path(image["requirements"], allow_hidden=True)
        except ValueError as error:
            raise ConfigError(str(error)) from error
    for name in ("cache_volume", "artifact_volume", "source_volume"):
        value = data["modal"][name]
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
            raise ConfigError(f"modal.{name} must be a valid volume name")
    if (
        len({data["modal"][name] for name in ("cache_volume", "artifact_volume", "source_volume")})
        != 3
    ):
        raise ConfigError("cache, artifact, and source volumes must have distinct names")
    _strings(data["modal"]["secrets"], "modal.secrets")
    name = data["modal"]["environment_name"]
    if name is not None and (not isinstance(name, str) or not name or "\x00" in name):
        raise ConfigError("modal.environment_name must be a nonempty string")


def discover(start=None):
    """Search the current directory and parents; a dedicated file wins at each level."""
    start = Path.cwd() if start is None else Path(start)
    start = checked_root(start)
    for root in (start, *start.parents):
        for name in ("skyrun.toml", "pyproject.toml"):
            candidate = root / name
            if candidate.is_file():
                if name == "pyproject.toml":
                    with candidate.open("rb") as stream:
                        if "skyrun" not in tomllib.load(stream).get("tool", {}):
                            continue
                return candidate
    raise ConfigError("no skyrun.toml or [tool.skyrun] found; run 'skyrun init'")


def load_config(path=None, *, start=None):
    try:
        path = discover(start) if path is None else Path(path).absolute()
        checked_root(path)
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
        if path.name == "pyproject.toml":
            raw = raw.get("tool", {}).get("skyrun")
            if raw is None:
                raise ConfigError("pyproject.toml has no [tool.skyrun] table")
        data = _normalize(raw)
        return Config(path.parent, data, path)
    except (OSError, tomllib.TOMLDecodeError, ValueError, TypeError) as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"cannot load configuration: {error}") from error
