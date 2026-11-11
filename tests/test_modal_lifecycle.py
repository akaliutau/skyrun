import contextlib
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from skyrun.errors import ConfigError
from skyrun.manifest import snapshot
from skyrun.providers import modal as backend
from skyrun.runner import make_spec


class RecordingSDK:
    def __init__(self):
        self.image_steps = []
        self.function_settings = {}
        self.volume_lookups = []
        self.canceled = False
        self.call = SimpleNamespace(object_id="fc-test", cancel=self.cancel)
        self.Image = SimpleNamespace(debian_slim=self.debian_slim, from_registry=self.from_registry)
        self.Volume = SimpleNamespace(from_name=self.volume)
        self.Secret = SimpleNamespace(from_name=lambda name, **kwargs: (name, kwargs))
        self.Queue = SimpleNamespace(ephemeral=lambda **kwargs: contextlib.nullcontext(object()))

    def cancel(self, **kwargs):
        self.canceled = kwargs["terminate_containers"]

    def debian_slim(self, **kwargs):
        self.image_steps.append(("debian_slim", kwargs))
        return self

    def from_registry(self, name, **kwargs):
        self.image_steps.append(("from_registry", name, kwargs))
        return self

    def __getattr__(self, name):
        if name in {
            "apt_install",
            "pip_install",
            "pip_install_from_requirements",
            "env",
            "add_local_file",
        }:

            def step(*args, **kwargs):
                self.image_steps.append((name, args, kwargs))
                return self

            return step
        raise AttributeError(name)

    def volume(self, name, **kwargs):
        self.volume_lookups.append((name, kwargs))
        return name

    def App(self, name, **kwargs):
        return self

    def function(self, **kwargs):
        self.function_settings = kwargs
        return lambda fn: SimpleNamespace(spawn=lambda *args: self.call)

    def run(self, **kwargs):
        return contextlib.nullcontext()

    def enable_output(self, *, show_progress):
        assert show_progress is False
        return contextlib.nullcontext()


def test_image_contains_runtime_allowlist_and_frozen_dependencies(
    config_factory, project, tmp_path, monkeypatch
):
    (project / "requirements.txt").write_text("numpy==2.2.6\n")
    config = config_factory(
        {
            "image": {
                "requirements": "requirements.txt",
                "base_image": "example/cuda:tag",
                "pip_packages": ["pandas==2.2.3"],
            }
        }
    )
    source = snapshot(config, tmp_path / "snapshot")
    sdk = RecordingSDK()
    monkeypatch.setattr(backend, "sdk", lambda: sdk)
    backend.build_image(config, source)
    mounts = [s[1] for s in sdk.image_steps if s[0] == "add_local_file"]
    assert {remote for _, remote in mounts} == {
        "/opt/skyrun/skyrun/" + name for name in backend.RUNTIME_FILES
    }
    assert ("pip_install_from_requirements", (str(source.requirements),), {}) in sdk.image_steps
    assert sdk.image_steps[0] == ("from_registry", "example/cuda:tag", {"add_python": "3.11"})
    assert all(local.is_file() for local, _ in mounts)


def test_provider_resource_settings_and_call_record(config_factory, tmp_path, monkeypatch):
    config = config_factory(
        {
            "run": {"gpu": "cpu", "cpu": 2, "memory_mib": 8192},
            "modal": {"environment_name": "dev", "secrets": ["training-secret"]},
        }
    )
    source = snapshot(config, tmp_path / "snapshot")
    spec = make_spec(config, source, "test-run")
    sdk = RecordingSDK()
    monkeypatch.setattr(backend, "sdk", lambda: sdk)
    monkeypatch.setattr(backend, "upload_source", lambda *args: 0)
    monkeypatch.setattr(backend, "wait_for_result", lambda *args: {"returncode": 0})
    destination = tmp_path / "run"
    result = backend.ModalProvider().run(config, source, spec, destination)
    assert result == {"returncode": 0}
    settings = sdk.function_settings
    assert settings["gpu"] is None
    assert settings["cpu"] == 2 and settings["memory"] == 8192
    assert settings["timeout"] == 780
    assert settings["retries"] == 0
    assert settings["include_source"] is False
    assert settings["secrets"] == [("training-secret", {"environment_name": "dev"})]
    assert all(options["environment_name"] == "dev" for _, options in sdk.volume_lookups)
    assert set(settings["volumes"]) == {"/source", "/artifacts", "/cache"}
    assert '"fc-test"' in (destination / "call.json").read_text()
    assert not sdk.canceled


@pytest.mark.parametrize(("gpu", "base_image"), [("cpu", None), ("T4", "python:3.11-slim")])
def test_provider_with_real_modal_output_streams_raw_workload(
    config_factory, project, tmp_path, monkeypatch, capsys, gpu, base_image
):
    modal = pytest.importorskip("modal")
    (project / "requirements.txt").write_text("packaging==25.0\n")
    config = config_factory(
        {
            "run": {"gpu": gpu},
            "image": {
                "base_image": base_image,
                "requirements": "requirements.txt",
                "apt_packages": ["git"],
                "pip_packages": ["typing-extensions==4.15.0"],
            },
            "modal": {"environment_name": "dev", "secrets": ["training-secret"]},
        }
    )
    source = snapshot(config, tmp_path / "snapshot")
    spec = make_spec(config, source, "test-run")
    raw = "step 1/2\r\x1b[2Kstep 2/2 🙂".encode()
    partitions = {"events": ["ready"], "output": [raw[:25], raw[25:]]}
    commands = []
    channel = SimpleNamespace(
        get_many=lambda n, **kwargs: partitions.pop(kwargs["partition"], []),
        put=lambda value, **kwargs: commands.append((kwargs["partition"], value)),
    )
    call = create_autospec(modal.FunctionCall, instance=True)
    call.object_id = "fc-test"
    # Use real SDK image, Volume, Secret, and Function definitions. Replace only
    # operations that would contact Modal, retaining their SDK signatures.
    monkeypatch.setattr(
        modal.App,
        "run",
        create_autospec(modal.App.run, return_value=contextlib.nullcontext()),
    )
    monkeypatch.setattr(
        modal.Queue,
        "ephemeral",
        create_autospec(modal.Queue.ephemeral, return_value=contextlib.nullcontext(channel)),
    )
    monkeypatch.setattr(
        modal.Function, "spawn", create_autospec(modal.Function.spawn, return_value=call)
    )

    def get_result(*, timeout):
        if hasattr(modal.output, "OutputManager"):
            manager = modal.output.OutputManager.get()
            assert not manager.is_enabled
            manager.print("Modal status must stay quiet")
        return {"returncode": 0}

    call.get.side_effect = get_result
    monkeypatch.setattr(backend, "upload_source", lambda *args: 0)
    result = backend.ModalProvider().run(config, source, spec, tmp_path / "run")
    assert result == {"returncode": 0}
    assert commands == [("commands", "start")]
    output = capsys.readouterr().out
    assert output.endswith(raw.decode())
    assert "Modal status must stay quiet" not in output
    call.cancel.assert_not_called()


@pytest.mark.parametrize("error", [KeyboardInterrupt, TimeoutError])
@pytest.mark.parametrize("real_output", [False, True])
def test_emergency_or_transport_failure_cancels_and_leaves_record(
    config_factory, tmp_path, monkeypatch, error, real_output
):
    config = config_factory()
    source = snapshot(config, tmp_path / "snapshot")
    spec = make_spec(config, source, "test-run")
    sdk = RecordingSDK()
    manager_type = None
    if real_output:
        modal = pytest.importorskip("modal")
        sdk.enable_output = modal.enable_output
        sdk.output = modal.output
        manager_type = getattr(modal.output, "OutputManager", None)
        if manager_type is not None:
            previous_manager = manager_type.get()
    monkeypatch.setattr(backend, "sdk", lambda: sdk)
    monkeypatch.setattr(backend, "upload_source", lambda *args: 0)

    def fail(*args):
        raise error()

    monkeypatch.setattr(backend, "wait_for_result", fail)
    destination = tmp_path / "run"
    with pytest.raises(error):
        backend.ModalProvider().run(config, source, spec, destination)
    assert sdk.canceled
    assert (destination / "interrupted.json").is_file()
    if manager_type is not None:
        assert manager_type.get() is previous_manager


def test_missing_modal_dependency_gives_install_command(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "modal", None)
    with pytest.raises(ConfigError, match=r"skyrun\[modal\]"):
        backend.sdk()
