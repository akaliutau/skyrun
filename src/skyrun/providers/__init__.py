"""Provider registry. Third-party adapters register under 'skyrun.providers'."""

from importlib.metadata import entry_points
from typing import Protocol

from skyrun.errors import ConfigError


class Provider(Protocol):
    def run(self, config, source, spec, destination) -> dict:
        """Execute once, streaming output and honoring local stop requests."""
        ...

    def download(self, config, run_id, destination) -> int:
        """Stream committed artifacts to the destination without allocating compute."""
        ...


def get_provider(name) -> Provider:
    if name == "modal":
        from skyrun.providers.modal import ModalProvider

        return ModalProvider()
    matches = list(entry_points(group="skyrun.providers", name=name))
    if len(matches) != 1:
        detail = "not installed" if not matches else "registered by multiple packages"
        raise ConfigError(f"provider {name!r} is {detail}")
    provider = matches[0].load()()
    if not callable(getattr(provider, "run", None)) or not callable(
        getattr(provider, "download", None)
    ):
        raise ConfigError(f"provider {name!r} must implement run() and download()")
    return provider
