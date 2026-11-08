from types import SimpleNamespace

import pytest

from skyrun import providers
from skyrun.errors import ConfigError


def test_plugin_entry_point_contract(monkeypatch):
    class CustomProvider:
        def run(self, *args):
            return {}

        def download(self, *args):
            return 0

    entry = SimpleNamespace(load=lambda: CustomProvider)
    monkeypatch.setattr(providers, "entry_points", lambda **kwargs: [entry])
    assert isinstance(providers.get_provider("custom"), CustomProvider)


@pytest.mark.parametrize("entries", [[], [object(), object()]])
def test_unknown_or_ambiguous_provider_fails(monkeypatch, entries):
    monkeypatch.setattr(providers, "entry_points", lambda **kwargs: entries)
    with pytest.raises(ConfigError):
        providers.get_provider("custom")


def test_provider_must_support_execution_and_recovery(monkeypatch):
    monkeypatch.setattr(
        providers, "entry_points", lambda **kwargs: [SimpleNamespace(load=lambda: object)]
    )
    with pytest.raises(ConfigError, match="implement"):
        providers.get_provider("custom")
