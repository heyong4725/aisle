"""MON-6/MON-12/MON-13: source-bound typed policy execution in a separate process."""

import hashlib
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_typed_node_requests import _node

pytestmark = pytest.mark.unit


def _run(
    source,
    *,
    digest=None,
    module_name="aisle.nodes.segmented_pose",
    preload=False,
    configuration=None,
):
    from aisle.harness.typed_node_requests import TypedNodeRequests
    from aisle.monolith.supervisor import _DeadlinePipe
    from aisle.monolith.wire import receive, send

    root = Path(__file__).resolve().parents[2] / "src"
    bootstrap = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from aisle.harness.typed_node_worker import serve; "
        "raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))"
    )
    if preload:
        bootstrap = bootstrap.replace(
            "from aisle.harness.typed_node_worker",
            "import aisle.turn_node; from aisle.harness.typed_node_worker",
        )
    raw, node = _node()
    host = TypedNodeRequests(node, outputs={"result"})
    messages = []
    with subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", bootstrap, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            deadline = time.monotonic() + 10
            incoming = _DeadlinePipe(process.stdout, deadline, time.monotonic)
            outgoing = _DeadlinePipe(process.stdin, deadline, time.monotonic)
            send(
                outgoing,
                {
                    "op": "init",
                    "module": module_name,
                    "source": source,
                    "source_sha256": digest or hashlib.sha256(source.encode()).hexdigest(),
                    **({"configuration": configuration} if configuration is not None else {}),
                },
            )
            while True:
                message = receive(incoming)
                messages.append(message)
                if message["kind"] == "node":
                    send(outgoing, host.handle(message["request"]))
                elif message["kind"] in ("finished", "error"):
                    break
            rc = process.wait(timeout=10)
            log = process.stderr.read().decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    return rc, messages, log, raw


@pytest.mark.parametrize(
    "module_name",
    [
        "aisle.nodes.segmented_pose",
        "aisle.nodes.l2_pose",
        "aisle.nodes.grasp_topdown",
        "aisle.nodes.ik_trajectory",
        "aisle.nodes.task_state_machine",
    ],
)
def test_worker_runs_authored_main_with_remote_node_and_separate_logs(module_name):
    """MON-6/MON-12: constructor/imports/callbacks stay in the child; turn authority stays host."""
    source = """
import sys
from dataclasses import dataclass
import pyarrow as pa
from aisle.turn_node import Node
from aisle.topics import make_sender
print("authored module imported")
@dataclass
class Policy:
    value: int = 9

def main():
    node = Node()
    sender = make_sender(node)
    for event in node:
        sender("result", pa.array([Policy().value], type=pa.int32()), event["metadata"])
    assert "dora" not in sys.modules
    print("authored main finished")
if __name__ == "__main__":
    main()
"""
    rc, messages, log, raw = _run(source, module_name=module_name)
    assert rc == 0, (messages, log)
    assert messages[0]["kind"] == "ready"
    assert messages[0]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert messages[-1]["kind"] == "finished"
    assert messages[-1]["input_exhausted"] is True
    assert "authored module imported" in log
    assert "authored main finished" in log
    assert [row[0] for row in raw.sent] == ["result", "turn_done"]
    assert raw.sent[0][1].to_pylist() == [9]


def test_worker_rejects_source_drift_before_executing_authored_code():
    """MON-13: a mismatching source receipt cannot reach imports or main."""
    rc, messages, log, raw = _run("print('must not execute')", digest="0" * 64)
    assert rc == 1
    assert [message["kind"] for message in messages] == ["error"]
    assert messages[0]["error"]["category"] == "protocol"
    assert "must not execute" not in log
    assert not raw.sent


@pytest.mark.parametrize("source", ["invalid python : :", "raise ValueError('authored failure')"])
def test_worker_retains_ordinary_authored_errors_without_turn_completion(source):
    """MON-12: failed module loading emits a terminal error without fabricating a watermark."""
    rc, messages, log, raw = _run(source)
    assert rc == 1
    assert messages[-1]["kind"] == "error"
    assert messages[-1]["error"]["category"] == "module"
    assert not raw.sent


def test_worker_does_not_inherit_loader_future_annotation_semantics():
    """MON-2: source execution retains its own Python semantics, including annotation errors."""
    rc, messages, log, raw = _run("value: undefined_annotation = 1\ndef main(): pass\n")
    assert rc == 1
    assert messages[-1]["error"]["type"] == "NameError"
    assert not raw.sent


def test_worker_early_return_does_not_fabricate_node_or_input_completion():
    """MON-12: an authored main that returns early cannot attest consumed host inputs."""
    rc, messages, log, raw = _run("def main(): print('returned without Node')\nmain()\n")
    assert rc == 0
    assert messages[-1]["kind"] == "finished"
    assert messages[-1]["node_created"] is False
    assert messages[-1]["input_exhausted"] is False
    assert "returned without Node" in log
    assert not raw.sent


def test_worker_refuses_a_second_node_capability():
    """MON-6/MON-13: one authored process cannot create an additional node connection."""
    rc, messages, log, raw = _run(
        "from aisle.turn_node import Node\ndef main():\n    Node()\n    Node()\nmain()\n"
    )
    assert rc == 1
    assert messages[-1]["error"]["category"] == "protocol"
    assert not raw.sent


def test_worker_preserves_python_script_entry_guard():
    """MON-2: Dora Python nodes execute as scripts, including authored __main__ guards."""
    source = """
if __name__ == "__main__":
    import sys
    from aisle.turn_node import Node
    for event in Node():
        pass
    assert sys.argv == ["src/aisle/nodes/segmented_pose.py"]
    print("script guard executed")
"""
    rc, messages, log, raw = _run(source)
    assert rc == 0, messages
    assert "script guard executed" in log
    assert messages[-1]["input_exhausted"] is True
    assert raw.sent[-1][0] == "turn_done"


@pytest.mark.parametrize("source", ["raise SystemExit(0)", "raise SystemExit()"])
def test_worker_preserves_successful_script_exit_without_claiming_input_completion(source):
    """MON-2/MON-12: successful Python exit retains its status and unfinished input marker."""
    rc, messages, log, raw = _run(source)
    assert rc == 0
    assert messages[-1]["kind"] == "finished"
    assert messages[-1]["input_exhausted"] is False
    assert not raw.sent


def test_worker_refuses_bootstrap_that_already_loaded_trusted_turn_wrapper():
    """MON-6/MON-13: a stale bootstrap cannot substitute the real wrapper for the facade."""
    rc, messages, log, raw = _run("print('must not run')", preload=True)
    assert rc == 1
    assert messages[0]["kind"] == "error"
    assert messages[0]["error"]["category"] == "protocol"
    assert "must not run" not in log
    assert not raw.sent


def test_worker_applies_graph_configuration_inside_child(monkeypatch):
    """MON-2/MON-6: authored environment and argv apply only after worker bootstrap."""
    import os

    monkeypatch.setenv("AISLE_TASK_TIER", "host-sentinel")
    source = (
        "import os,sys\n"
        "assert os.environ['AISLE_TASK_TIER']=='T1'\n"
        "assert os.environ['PYTHONPATH']=='/authored/value'\n"
        "assert sys.argv[1:]==['--label', 'two words', '$(literal)']\n"
        "from aisle.turn_node import Node\n"
        "list(Node())\n"
    )
    rc, messages, _, _ = _run(
        source,
        configuration={
            "environment": {"AISLE_TASK_TIER": "T1", "PYTHONPATH": "/authored/value"},
            "arguments": ["--label", "two words", "$(literal)"],
        },
    )
    assert rc == 0, messages
    assert messages[-1]["input_exhausted"]
    assert os.environ["AISLE_TASK_TIER"] == "host-sentinel"


@pytest.mark.parametrize(
    "configuration",
    [
        {"environment": {"BAD=NAME": "x"}, "arguments": []},
        {"environment": {"X": "bad\x00value"}, "arguments": []},
        {"environment": {}, "arguments": "--not-a-list"},
        {"environment": {}, "arguments": [1]},
        {"environment": {}, "arguments": [], "build": "do something"},
    ],
)
def test_worker_refuses_malformed_configuration_before_authored_execution(configuration):
    """MON-6: malformed configuration cannot reach authored module initialization."""
    rc, messages, _, _ = _run("raise AssertionError('authored ran')", configuration=configuration)
    assert rc == 1
    assert messages[-1]["error"]["category"] == "protocol"
    assert not any(message["kind"] == "ready" for message in messages)
