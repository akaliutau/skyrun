import copy
import json
import os

import pytest

from skyrun.cli import init_project
from skyrun.config import Config, load_config
from skyrun.runner import download, run


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SKYRUN_TEST_MODAL") != "1",
    reason="set SKYRUN_TEST_MODAL=1 to authorize the live Modal CPU smoke",
)
def test_live_modal_cpu_hello(tmp_path):
    project = init_project(tmp_path / "hello", example="hello")
    result = run(project / "skyrun.toml")
    assert result.returncode == 0
    assert (result.directory / "outputs/hello.json").is_file()
    assert "Hello from skyrun" in (result.directory / "_runner/console.log").read_text()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SKYRUN_TEST_GPU") != "1",
    reason="set SKYRUN_TEST_GPU=1 to authorize the live T4/sync/recovery smoke",
)
def test_live_modal_gpu_sync_and_recovery(tmp_path):
    project = init_project(tmp_path / "gpu", example="hello")
    (project / "state.json").write_text('{"runs": 0}\n')
    (project / "gpu.py").write_text("""import json
import subprocess
from pathlib import Path

gpu = subprocess.run(
    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
    check=True, capture_output=True, text=True, timeout=15,
).stdout.strip()
state = json.loads(Path("state.json").read_text())
state["runs"] += 1
Path("state.json").write_text(json.dumps(state) + "\\n")
Path("outputs/gpu.json").write_text(json.dumps({"gpu": gpu}) + "\\n")
print(gpu, flush=True)
""")
    config = load_config(project / "skyrun.toml")
    data = copy.deepcopy(config.data)
    data["sync"]["bidirectional"] = ["state.json"]
    config = Config(config.root, data, config.path)
    result = run(config, gpu="T4", timeout=60, command=["python", "gpu.py"])
    assert result.returncode == 0
    assert result.data["gpu_request"] == "T4"
    assert json.loads((project / "state.json").read_text()) == {"runs": 1}
    assert result.sync_report["applied"] == ["state.json"]
    report = json.loads((result.directory / "outputs/gpu.json").read_text())
    assert report["gpu"]
    recovered = download(result.run_id, config, output_dir=tmp_path / "recovered")
    assert (recovered / "outputs/gpu.json").read_bytes() == (
        result.directory / "outputs/gpu.json"
    ).read_bytes()
    assert json.loads((recovered / "_sync/files/state.json").read_text()) == {"runs": 1}
