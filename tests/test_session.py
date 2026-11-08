import io
import signal
from collections import defaultdict

import pytest

from skyrun.session import StopRequests, wait_for_result


class Channel:
    def __init__(self, *, ready=True, chunks=()):
        self.partitions = defaultdict(list)
        self.partitions["events"] = ["ready"] if ready else []
        self.partitions["output"] = list(chunks)
        self.sent = []

    def get_many(self, n, *, block, partition):
        values = self.partitions[partition][:n]
        del self.partitions[partition][:n]
        return values

    def put(self, value, *, partition):
        self.sent.append((partition, value))


class Call:
    def __init__(self, result=None, on_poll=None):
        self.result = result or {"returncode": 0}
        self.canceled = False
        self.on_poll = on_poll
        self.polls = 0

    def get(self, *, timeout):
        self.polls += 1
        if self.on_poll:
            self.on_poll(self.polls)
        if self.polls < 3:
            raise TimeoutError
        return self.result

    def cancel(self, *, terminate_containers):
        self.canceled = terminate_containers


SPEC = {
    "run_id": "test",
    "timeout_seconds": 10,
    "startup_timeout_seconds": 10,
    "shutdown_grace_seconds": 1,
}


def test_raw_chunks_and_split_unicode_are_streamed_without_newline():
    raw = "first\r🙂done".encode()
    channel = Channel(chunks=[raw[:7], raw[7:8], raw[8:]])
    stream = io.StringIO()
    assert wait_for_result(Call(), channel, SPEC, StopRequests(), stream=stream) == {
        "returncode": 0
    }
    assert stream.getvalue() == "first\r🙂done"
    assert channel.sent == [("commands", "start")]


def test_queued_cancellation_never_sends_start():
    stops = StopRequests()
    stops.level = 1
    channel, call = Channel(), Call()
    result = wait_for_result(call, channel, SPEC, stops)
    assert result["canceled_before_start"]
    assert result["returncode"] == 130
    assert call.canceled
    assert channel.sent == []


def test_stop_levels_forward_interrupt_then_kill():
    stops = StopRequests()

    def on_poll(count):
        stops.level = min(count, 2)

    channel = Channel()
    wait_for_result(Call(on_poll=on_poll), channel, SPEC, stops)
    assert channel.sent == [("commands", "start"), ("commands", "interrupt"), ("commands", "kill")]


def test_third_signal_is_emergency_interrupt():
    stops = StopRequests()
    stops._signal(signal.SIGINT, None)
    stops._signal(signal.SIGINT, None)
    with pytest.raises(KeyboardInterrupt):
        stops._signal(signal.SIGINT, None)


def test_signal_handlers_are_restored():
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    with StopRequests():
        assert signal.getsignal(signal.SIGINT) != previous[signal.SIGINT]
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
