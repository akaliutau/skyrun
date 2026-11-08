import json
import subprocess
import sys

import pytest

from skyrun.cli import LightGreyStream, init_project, main
from skyrun.errors import ConfigError


@pytest.mark.parametrize(
    "example,script", [("hello", "main.py"), ("training", "train.py"), ("inference", "demo.py")]
)
def test_packaged_examples_initialize_and_dry_run(tmp_path, monkeypatch, capsys, example, script):
    destination = tmp_path / example
    assert main(["init", str(destination), "--example", example]) == 0
    capsys.readouterr()
    monkeypatch.setitem(sys.modules, "modal", None)
    assert main(["run", "--config", str(destination / "skyrun.toml"), "--dry-run"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["command"][1] == script
    assert script in {entry["path"] for entry in spec["source"]["files"]}
    if example == "hello":
        result = subprocess.run(
            [sys.executable, script], cwd=destination, text=True, capture_output=True, check=True
        )
        assert "Hello from skyrun" in result.stdout
        assert (destination / "outputs/hello.json").is_file()
    else:
        assert spec["requirements_sha256"]


def test_initialization_checks_all_collisions_before_writing(tmp_path):
    (tmp_path / "main.py").write_text("my existing project")
    with pytest.raises(ConfigError, match="overwrite"):
        init_project(tmp_path, example="hello")
    assert not (tmp_path / "skyrun.toml").exists()
    assert (tmp_path / "main.py").read_text() == "my existing project"


def test_configuration_only_init_preserves_existing_code(tmp_path):
    (tmp_path / "main.py").write_text("existing code")
    init_project(tmp_path)
    assert (tmp_path / "main.py").read_text() == "existing code"
    assert (tmp_path / "skyrun.toml").is_file()


def test_module_cli_from_another_working_directory(tmp_path):
    project = init_project(tmp_path / "training", example="training")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "skyrun",
            "run",
            "--config",
            str(project / "skyrun.toml"),
            "--gpu",
            "cpu",
            "--dry-run",
            "--",
            "python",
            "train.py",
            "--steps",
            "4",
        ],
        cwd=elsewhere,
        text=True,
        capture_output=True,
        check=True,
    )
    spec = json.loads(result.stdout)
    assert spec["gpu_request"] is None
    assert spec["command"] == ["python", "train.py", "--steps", "4"]
    assert {entry["path"] for entry in spec["source"]["files"]} == {
        "train.py",
        "skyrun.toml",
        "requirements-gpu.txt",
    }
    assert "\x1b" not in result.stdout


def test_color_filter_preserves_split_cursor_escapes():
    import io

    output = io.StringIO()
    stream = LightGreyStream(output)
    assert stream.write("a\x1b[") == 3
    stream.write("1A\x1b[31mred\x1b[0m\r")
    assert "\x1b[1A" in output.getvalue()
    assert "\x1b[31m" not in output.getvalue()
    assert "red" in output.getvalue()


def test_malformed_options_fail_locally():
    with pytest.raises(SystemExit) as error:
        main(["--download", "x", "--dry-run"])
    assert error.value.code == 2
