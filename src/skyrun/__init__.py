"""Ordinary code, remote compute. Importing skyrun never contacts a provider."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("skyrun")
except PackageNotFoundError:
    __version__ = "0+unknown"


def __getattr__(name):
    # Keep the remote worker and offline CLI independent of optional SDK imports.
    if name in {"run", "download", "RunResult"}:
        from skyrun import runner

        return getattr(runner, name)
    if name == "load_config":
        from skyrun.config import load_config

        return load_config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["RunResult", "__version__", "download", "load_config", "run"]
