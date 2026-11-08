"""Local signal handling and a raw output stream, independent of Modal's log formatter."""

import codecs
import signal
import sys
import time


class StopRequests:
    """Keep the client connected for checkpointing and file downloads after Ctrl-C."""

    def __init__(self):
        self.level = 0
        self.previous = {}

    def _signal(self, signum, frame):
        self.level += 1
        if self.level >= 3:
            raise KeyboardInterrupt

    def __enter__(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.previous[signum] = signal.signal(signum, self._signal)
        return self

    def __exit__(self, *args):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


def wait_for_result(call, channel, spec, stops, *, stream=None):
    """Relay output without newline buffering; control and output use separate partitions."""
    stream = stream if stream is not None else sys.stdout
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    start_sent = False
    sent_level = 0
    last_waiting = time.monotonic()
    deadline = (
        last_waiting
        + spec["timeout_seconds"]
        + spec["startup_timeout_seconds"]
        + spec["shutdown_grace_seconds"]
        + 180
    )

    def drain():
        chunks = channel.get_many(64, block=False, partition="output")
        for data in chunks:
            stream.write(decoder.decode(data))
        if chunks:
            stream.flush()
        return bool(chunks)

    while True:
        if stops.level and not start_sent:
            call.cancel(terminate_containers=True)
            print(
                "Canceled before the workload started; no workload files were created.",
                flush=True,
            )
            return {
                **spec,
                "status": "canceled",
                "returncode": 130,
                "elapsed_seconds": 0,
                "started": False,
                "canceled_before_start": True,
                "forced": False,
            }
        if not start_sent:
            events = channel.get_many(8, block=False, partition="events")
            if "ready" in events:
                channel.put("start", partition="commands")
                start_sent = True
                print(
                    "Running. Ctrl-C: save and stop; twice: force the workload to stop.",
                    flush=True,
                )
        if start_sent and stops.level > sent_level:
            channel.put("kill" if stops.level >= 2 else "interrupt", partition="commands")
            sent_level = stops.level
            print(
                "\nForce requested; waiting for logs and output files."
                if sent_level >= 2
                else f"\nStop requested; allowing {spec['shutdown_grace_seconds']}s for cleanup and checkpoints.",
                flush=True,
            )
            deadline = time.monotonic() + spec["shutdown_grace_seconds"] + 120
        drain()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("queue/startup/workload/cleanup wait limit exceeded")
        try:
            result = call.get(timeout=min(0.25, remaining))
            while drain():
                pass
            stream.write(decoder.decode(b"", final=True))
            stream.flush()
            return result
        except TimeoutError:
            if not start_sent and time.monotonic() - last_waiting >= 10:
                print(f"[launcher] waiting for {spec['run_id']} to start", flush=True)
                last_waiting = time.monotonic()
