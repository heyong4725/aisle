"""MON-3/MON-4/MON-12: authored controllers execute across a real process boundary."""

import os
import select
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@contextmanager
def _worker(tmp_path, *, program=None):
    from aisle.monolith.wire import receive, send

    class DeadlineReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size):
            if not select.select([self.stream], [], [], 10)[0]:
                raise TimeoutError("worker fixture did not reply")
            return os.read(self.stream.fileno(), size)

    with (tmp_path / "worker.log").open("wb") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                program
                or (
                    "import sys; from aisle.monolith.worker import serve; "
                    "raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))"
                ),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            bufsize=0,
            start_new_session=True,
        )
        try:
            yield (
                child,
                lambda value: send(child.stdin, value),
                lambda: receive(DeadlineReader(child.stdout)),
            )
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            child.stdin.close()
            child.stdout.close()


def test_existing_expert_executes_in_worker_with_parent_primitives(tmp_path):
    """MON-4/MON-12: unchanged expert construction and callbacks use parent-owned primitives.

    This launches a real engineering child without an OS confinement adapter;
    it proves RPC integration only, not sandbox or baseline acceptance.
    """
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequests

    service = PrimitiveRequests(Primitives._load("franka"))
    source = (Path(__file__).resolve().parents[2] / "experts/monolithic/expert_t1.py").read_text()
    with _worker(tmp_path) as (child, send, receive):

        def command(message):
            send(message)
            while True:
                reply = receive()
                if reply["kind"] != "primitive":
                    assert reply["id"] == message["id"]
                    return reply
                value = service.dispatch(reply["request"])
                send({"kind": "primitive_reply", "id": reply["id"], "reply": value})

        ready = command({"id": 1, "op": "init", "source": source, "filename": "expert_t1.py"})
        assert ready["kind"] == "result"
        assert ready["value"]["api_version"] == "1.0"
        goal = {
            "name": "episode_goal",
            "payload": {"target_med": service._objects[0].med_names[0]},
            "sim_time_ns": 0,
            "goal_id": "fixture",
        }
        assert command({"id": 2, "op": "event", "event": goal})["value"] == []
        goal.update(name="tick", payload=1, sim_time_ns=1_000_000_000)
        assert command({"id": 3, "op": "event", "event": goal})["value"] == [
            {"feedback": {"t": 1, "phase": "executing", "retries": 0}}
        ]
        assert command({"id": 4, "op": "close"}) == {"kind": "result", "id": 4, "value": None}
        assert child.wait(timeout=10) == 0
        assert service.calls >= 3


@pytest.mark.parametrize(
    "source,kind",
    [
        ("invalid python :::", "SyntaxError"),
        (
            "API_VERSION='1.0'\nclass Controller:\n"
            " def __init__(self,p,log): raise ValueError('constructor failed')\n",
            "ValueError",
        ),
    ],
)
def test_worker_retains_constructor_failures(tmp_path, source, kind):
    """MON-12/MON-13: construction failures produce a terminal error reply and failed process."""
    with _worker(tmp_path) as (child, send, receive):
        send({"id": 1, "op": "init", "source": source, "filename": "fixture.py"})
        reply = receive()
        if reply["kind"] == "primitive":
            send({"kind": "primitive_reply", "id": reply["id"], "reply": {"value": "1.0"}})
            reply = receive()
        assert reply["kind"] == "error" and reply["id"] == 1
        assert reply["error"]["kind"] == kind
        assert child.wait(timeout=10) == 1


def test_worker_keeps_prints_out_of_protocol_and_reports_callback_error(tmp_path):
    """MON-12/MON-13: module logs stay separate; callback failures are terminal."""
    source = """API_VERSION = '1.0'
print('module-loaded')
class Controller:
    def __init__(self, primitives, log):
        log('constructed')
    def on_event(self, event):
        print('callback-entered')
        raise ValueError('callback failed')
"""
    with _worker(tmp_path) as (child, send, receive):
        send({"id": 1, "op": "init", "source": source, "filename": "fixture.py"})
        request = receive()
        assert request["kind"] == "primitive"
        send({"kind": "primitive_reply", "id": request["id"], "reply": {"value": "1.0"}})
        assert receive()["kind"] == "result"
        send({"id": 2, "op": "event", "event": {"name": "tick", "payload": 1}})
        reply = receive()
        assert reply == {
            "kind": "error",
            "id": 2,
            "error": {"category": "module", "kind": "ValueError", "message": "callback failed"},
        }
        assert child.wait(timeout=10) == 1
    assert (tmp_path / "worker.log").read_text().splitlines() == [
        "module-loaded",
        "constructed",
        "callback-entered",
    ]


@pytest.mark.parametrize("bad_id", [True, 2])
def test_worker_refuses_mismatched_primitive_reply(tmp_path, bad_id):
    """MON-8/MON-13: reply IDs have exact integer identity and cannot cross requests."""
    with _worker(tmp_path) as (child, send, receive):
        send({"id": 1, "op": "init", "source": "API_VERSION='1.0'", "filename": "fixture.py"})
        assert receive()["id"] == 1
        send({"kind": "primitive_reply", "id": bad_id, "reply": {"value": "1.0"}})
        reply = receive()
        assert reply["kind"] == "error" and reply["error"]["category"] == "protocol"
        assert child.wait(timeout=10) == 1


def test_worker_refuses_event_before_initialization(tmp_path):
    """MON-8/MON-13: no callback executes without the initial source binding."""
    with _worker(tmp_path) as (child, send, receive):
        send({"id": 1, "op": "event", "event": {"name": "tick", "payload": 1}})
        reply = receive()
        assert reply["kind"] == "error" and reply["error"]["category"] == "protocol"
        assert child.wait(timeout=10) == 1


def test_worker_never_reinitializes_after_a_completed_constructor(tmp_path):
    """MON-8/MON-13: constructor return values cannot reopen source admission."""
    source = "API_VERSION='1.0'\ndef Controller(p, log): return None\n"
    with _worker(tmp_path) as (child, send, receive):
        send({"id": 1, "op": "init", "source": source, "filename": "first.py"})
        request = receive()
        send({"kind": "primitive_reply", "id": request["id"], "reply": {"value": "1.0"}})
        assert receive()["kind"] == "result"
        send({"id": 2, "op": "init", "source": source, "filename": "second.py"})
        reply = receive()
        assert reply["kind"] == "error" and reply["error"]["category"] == "protocol"
        assert child.wait(timeout=10) == 1
