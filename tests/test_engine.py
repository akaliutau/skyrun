import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from skyrun.remote.engine import run_workload

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the remote worker runs on POSIX")


def execute(tmp_path, code, *, terminal=False, control=None, emit=None, **overrides):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text(code)
    spec = {
        "source": {"files": []},
        "command": [sys.executable, "main.py"],
        "output_dirs": ["outputs", "checkpoints"],
        "timeout_seconds": 10,
        "shutdown_grace_seconds": 1,
        "terminal": terminal,
        **overrides,
    }
    result = run_workload(
        spec,
        source,
        tmp_path / "workspace",
        tmp_path / "artifacts",
        tmp_path / "cache",
        poll_control=control,
        emit=emit,
    )
    return result, tmp_path / "artifacts"


def test_success_outputs_environment_and_no_shell(tmp_path):
    code = """
import os, sys
from pathlib import Path
assert os.environ['MY_SETTING'] == 'a value'
assert sys.argv[1] == '; echo not-a-shell'
Path('outputs/value.txt').write_text('saved')
print('success', flush=True)
"""
    result, artifacts = execute(
        tmp_path,
        code,
        command=[sys.executable, "main.py", "; echo not-a-shell"],
        environment={"MY_SETTING": "a value"},
    )
    assert result["status"] == "completed"
    assert result["returncode"] == 0
    assert (artifacts / "outputs/value.txt").read_text() == "saved"
    assert b"success" in (artifacts / "_runner/console.log").read_bytes()
    assert json.loads((artifacts / "_runner/result.json").read_text())["returncode"] == 0


@pytest.mark.parametrize("terminal", [False, True])
def test_tty_and_raw_progress_bytes(tmp_path, terminal):
    code = "import os, sys\nprint('TTY=' + str(sys.stdout.isatty()), flush=True)\nos.write(1, 'first\\rnext 🙂\\n'.encode())\n"
    chunks = []
    result, artifacts = execute(tmp_path, code, terminal=terminal, emit=lambda b: chunks.append(b))
    raw = (artifacts / "_runner/console.log").read_bytes()
    assert f"TTY={terminal}".encode() in raw
    assert "first\rnext 🙂\n".encode() in raw
    assert b"".join(chunks) == raw
    assert result["returncode"] == 0


def test_backpressure_keeps_full_log(tmp_path):
    result, artifacts = execute(tmp_path, "print('x' * 100000)\n", emit=lambda b: False)
    raw = (artifacts / "_runner/console.log").read_bytes()
    assert len(raw) == 100001
    assert result["stream_dropped_bytes"] == len(raw)


def test_nonzero_exit_still_keeps_artifacts(tmp_path):
    result, artifacts = execute(
        tmp_path,
        "from pathlib import Path\nPath('outputs/saved.txt').write_text('before failure')\nraise SystemExit(7)\n",
    )
    assert result["status"] == "failed"
    assert result["returncode"] == 7
    assert (artifacts / "outputs/saved.txt").exists()


def test_missing_executable_is_recorded(tmp_path):
    result, artifacts = execute(tmp_path, "pass", command=["/nonexistent/skyrun-executable"])
    assert result["returncode"] == 1
    assert not result["started"]
    assert "FileNotFoundError" in result["error"]
    assert b"FileNotFoundError" in (artifacts / "_runner/console.log").read_bytes()


def test_first_interrupt_saves_checkpoint(tmp_path):
    ready = False

    def emit(chunk):
        nonlocal ready
        ready = ready or b"READY" in chunk

    code = """
import time
from pathlib import Path
try:
    print('READY', flush=True)
    time.sleep(30)
except KeyboardInterrupt:
    print('STOPPING', flush=True)
finally:
    Path('checkpoints/last.txt').write_text('checkpoint')
"""
    result, artifacts = execute(
        tmp_path, code, control=lambda: "interrupt" if ready else None, emit=emit
    )
    assert result["status"] == "canceled"
    assert result["returncode"] == 130
    assert result["workload_returncode"] == 0
    assert not result["forced"]
    assert (artifacts / "checkpoints/last.txt").read_text() == "checkpoint"
    assert b"STOPPING" in (artifacts / "_runner/console.log").read_bytes()


def test_timeout_uses_graceful_cleanup(tmp_path):
    code = "import time\nfrom pathlib import Path\ntry:\n    time.sleep(30)\nfinally:\n    Path('outputs/cleanup').write_text('done')\n"
    result, artifacts = execute(tmp_path, code, timeout_seconds=1)
    assert result["status"] == "timed_out"
    assert result["returncode"] == 124
    assert not result["forced"]
    assert (artifacts / "outputs/cleanup").exists()


@pytest.mark.parametrize("command", ["kill", "interrupt"])
def test_forced_stop_kills_uncooperative_workload(tmp_path, command):
    ready = False

    def emit(chunk):
        nonlocal ready
        ready = ready or b"READY" in chunk

    code = "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\nprint('READY', flush=True)\ntime.sleep(30)\n"
    result, artifacts = execute(
        tmp_path, code, control=lambda: command if ready else None, emit=emit
    )
    assert result["returncode"] == 130
    assert result["forced"]
    assert result["workload_returncode"] == -signal.SIGKILL
    assert result["elapsed_seconds"] < 5


def test_prestart_cancel_never_starts_process(tmp_path):
    result, artifacts = execute(
        tmp_path, "raise RuntimeError('should not run')", control=lambda: "interrupt"
    )
    assert result["returncode"] == 130
    assert not result["started"]
    assert (artifacts / "_runner/console.log").read_bytes() == b""


def test_parent_exit_terminates_remaining_child_workers(tmp_path):
    code = """
import subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path('outputs/child-pid').write_text(str(child.pid))
"""
    result, artifacts = execute(tmp_path, code)
    assert result["returncode"] == 0
    pid = int((artifacts / "outputs/child-pid").read_text())
    proc = Path(f"/proc/{pid}/stat")
    if sys.platform == "linux":
        deadline = time.monotonic() + 2
        while proc.exists() and proc.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not proc.exists() or proc.read_text().split()[2] == "Z"
