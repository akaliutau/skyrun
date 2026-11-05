"""Infrastructure adapter. Workloads never import this module or Modal."""

import codecs
import contextlib
import errno
import json
import os
import queue
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from sync_manifest import inside, output_paths


def signal_group(process, signum):
    if process is not None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signum)


def terminal_pair(columns, rows):
    """A real output terminal lets unmodified tqdm/TTY detection work normally."""
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    attributes = termios.tcgetattr(slave)
    attributes[1] &= ~termios.ONLCR
    termios.tcsetattr(slave, termios.TCSANOW, attributes)
    return master, slave


def run_workload(
    spec, source, workspace, artifacts, cache, *, poll_control=None, emit=None
):
    """Stream an ordinary subprocess; interrupt it cooperatively before a forced kill."""
    source, workspace, artifacts, cache = map(
        Path, (source, workspace, artifacts, cache)
    )
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, workspace, dirs_exist_ok=True)
    metadata = artifacts / "_runner"
    metadata.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    for relative in output_paths(spec["output_dirs"]):
        link = inside(workspace, str(relative))
        target = inside(artifacts, str(relative))
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
    (metadata / "source.json").write_text(json.dumps(spec["source"], indent=2) + "\n")
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(workspace),
            "HF_HOME": str(cache / "huggingface"),
            "XDG_CACHE_HOME": str(cache),
            "TORCH_HOME": str(cache / "torch"),
            "TERM": "xterm-256color",
        }
    )
    env.update(spec.get("environment", {}))
    started = time.monotonic()
    process = None
    stop_reason = None
    stop_deadline = None
    next_control_poll = started
    grace = spec.get("shutdown_grace_seconds", 60)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    result = {
        **spec,
        "status": "failed",
        "returncode": 1,
        "started": False,
        "forced": False,
        "stream_dropped_bytes": 0,
    }

    def send(data):
        if emit is None:
            sys.stdout.write(decoder.decode(data))
            sys.stdout.flush()
        elif emit(data) is False:
            result["stream_dropped_bytes"] += len(data)

    try:
        with (
            (metadata / "console.log").open("wb") as log,
            contextlib.ExitStack() as resources,
        ):
            command = poll_control() if poll_control else None
            if command in {"interrupt", "kill"}:
                result.update(status="canceled", returncode=130)
                return result
            selector = resources.enter_context(selectors.DefaultSelector())
            terminal = spec.get("terminal", True)
            slave = None
            if terminal:
                master, slave = terminal_pair(
                    spec.get("terminal_columns", 100), spec.get("terminal_rows", 24)
                )
                resources.callback(os.close, master)
            try:
                process = subprocess.Popen(
                    spec["command"],
                    cwd=workspace,
                    env=env,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=slave if terminal else subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            finally:
                if slave is not None:
                    os.close(slave)
            result["started"] = True
            if terminal:
                reader = master
            else:
                resources.enter_context(process.stdout)
                reader = process.stdout.fileno()
            selector.register(reader, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                command = None
                if poll_control and now >= next_control_poll:
                    command = poll_control()
                    now = time.monotonic()
                    next_control_poll = now + 0.25
                if process.poll() is None:
                    if stop_reason is None and (
                        command in {"interrupt", "kill"}
                        or now - started >= spec["timeout_seconds"]
                    ):
                        stop_reason = "canceled" if command else "timed_out"
                        stop_deadline = now + grace
                        signal_group(process, signal.SIGINT)
                    if command == "kill" or (
                        stop_deadline is not None and now >= stop_deadline
                    ):
                        result["forced"] = True
                        signal_group(process, signal.SIGKILL)
                else:
                    signal_group(process, signal.SIGKILL)
                for key, _ in selector.select(timeout=0.1):
                    try:
                        data = os.read(key.fd, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO or not terminal:
                            raise
                        data = b""
                    if data:
                        log.write(data)
                        log.flush()
                        send(data)
                    else:
                        selector.unregister(key.fd)
            code = process.wait()
            result["workload_returncode"] = code
            result["returncode"] = (
                130
                if stop_reason == "canceled"
                else 124
                if stop_reason == "timed_out"
                else 128 - code
                if code < 0
                else code
            )
            result["status"] = stop_reason or ("completed" if code == 0 else "failed")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        trace = traceback.format_exc().encode()
        with (metadata / "console.log").open("ab") as log:
            log.write(trace)
        send(trace)
    finally:
        signal_group(process, signal.SIGKILL)
        if process is not None:
            process.wait()
        if emit is None:
            sys.stdout.write(decoder.decode(b"", final=True))
            sys.stdout.flush()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (metadata / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def execute(spec, channel):
    """The control handshake prevents queued cancellation from starting a workload."""
    import modal

    cache = modal.Volume.from_name(spec["cache_volume"])
    artifacts = modal.Volume.from_name(spec["artifact_volume"])
    channel.put("ready", partition="events")
    try:
        try:
            initial_command = channel.get(timeout=60, partition="commands")
        except queue.Empty:
            initial_command = "interrupt"

        def poll_control():
            if initial_command != "start":
                return "interrupt"
            commands = channel.get_many(16, block=False, partition="commands")
            return (
                "kill"
                if "kill" in commands
                else "interrupt"
                if "interrupt" in commands
                else None
            )

        def emit(data):
            try:
                channel.put(data, block=False, partition="output")
                return True
            except queue.Full:
                return False

        with tempfile.TemporaryDirectory(prefix="gpu-starter-") as workspace:
            return run_workload(
                spec,
                "/opt/gpu-starter/project",
                workspace,
                Path("/artifacts/runs") / spec["run_id"],
                "/cache",
                poll_control=poll_control,
                emit=emit,
            )
    finally:
        artifacts.commit()
        cache.commit()
