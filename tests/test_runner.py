import json
import shutil

import pytest

from skyrun.errors import SyncConflict
from skyrun.remote.engine import run_workload
from skyrun.runner import download, run


class LocalAdapter:
    """Test the whole orchestration with real subprocesses and local artifact transport."""

    def __init__(self, root, on_run=None):
        self.root = root
        self.on_run = on_run
        self.executions = 0

    def run(self, config, source, spec, destination):
        self.executions += 1
        if self.on_run:
            self.on_run(config)
        return run_workload(
            spec,
            source.project,
            self.root / "workspace",
            self.root / spec["run_id"],
            self.root / "cache",
        )

    def download(self, config, run_id, destination):
        shutil.copytree(self.root / run_id, destination, dirs_exist_ok=True)


def test_public_api_runs_downloads_and_syncs(config_factory, project, tmp_path, monkeypatch):
    (project / "state.json").write_text("start")
    (project / "main.py").write_text(
        "from pathlib import Path\nPath('state.json').write_text('finished')\nPath('outputs/result').write_text('saved')\n"
    )
    config = config_factory(
        {
            "run": {"gpu": "cpu"},
            "sync": {"bidirectional": ["state.json"]},
            "workload": {"terminal": False},
        }
    )
    adapter = LocalAdapter(tmp_path / "remote")
    monkeypatch.setattr("skyrun.runner.get_provider", lambda name: adapter)
    result = run(config)
    assert result.returncode == 0
    assert result.status == "completed"
    assert result.sync_report["applied"] == ["state.json"]
    assert (project / "state.json").read_text() == "finished"
    assert (result.directory / "outputs/result").read_text() == "saved"
    assert json.loads((result.directory / "submitted.json").read_text())["command"] == [
        "python",
        "main.py",
    ]
    (result.directory / "outputs/result").unlink()
    assert download(result.run_id, config) == result.directory
    assert adapter.executions == 1
    assert (result.directory / "outputs/result").read_text() == "saved"


def test_end_to_end_conflict_retains_downloaded_work(
    config_factory, project, tmp_path, monkeypatch
):
    (project / "state.json").write_text("original")
    (project / "main.py").write_text(
        "from pathlib import Path\nPath('state.json').write_text('remote')\n"
    )
    config = config_factory(
        {"sync": {"bidirectional": ["state.json"]}, "workload": {"terminal": False}}
    )
    adapter = LocalAdapter(
        tmp_path / "remote", on_run=lambda c: (c.project / "state.json").write_text("local edit")
    )
    monkeypatch.setattr("skyrun.runner.get_provider", lambda name: adapter)
    with pytest.raises(SyncConflict):
        run(config)
    assert (project / "state.json").read_text() == "local edit"
    run_dir = next((project / "runs").iterdir())
    assert (run_dir / "_sync/files/state.json").read_text() == "remote"
    assert (run_dir / "result.json").is_file()


def test_sync_can_be_deferred(config_factory, project, tmp_path, monkeypatch):
    (project / "state.json").write_text("original")
    (project / "main.py").write_text(
        "from pathlib import Path\nPath('state.json').write_text('remote')\n"
    )
    config = config_factory(
        {"sync": {"bidirectional": ["state.json"]}, "workload": {"terminal": False}}
    )
    monkeypatch.setattr(
        "skyrun.runner.get_provider", lambda name: LocalAdapter(tmp_path / "remote")
    )
    result = run(config, sync=False)
    assert (project / "state.json").read_text() == "original"
    assert (result.directory / "_sync/files/state.json").read_text() == "remote"
