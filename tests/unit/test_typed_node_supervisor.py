"""MON-6/MON-12/MON-13: typed worker results are checked against host-owned state."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_typed_node_requests import _node

pytestmark = pytest.mark.unit


def _spawn(tmp_path, script=None):
    root = Path(__file__).resolve().parents[2] / "src"
    bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); "
    if script is None:
        bootstrap += (
            "from aisle.harness.typed_node_worker import serve; "
            "raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))"
        )
    else:
        bootstrap += "exec(sys.argv[2])"
    log = (tmp_path / "stderr.log").open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            bootstrap,
            str(root),
            *([] if script is None else [script]),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        start_new_session=True,
    )
    return process, log


def test_supervisor_retains_real_worker_and_host_turn_completion(tmp_path):
    """MON-6/MON-12: completion requires host input exhaustion and child exit."""
    from aisle.harness.typed_node_supervisor import supervise_typed_worker

    source = """
import pyarrow as pa
from aisle.turn_node import Node
node = Node()
for event in node:
    node.send_output("result", pa.array([4]), {})
"""
    raw, node = _node()
    process, log = _spawn(tmp_path)
    try:
        result = supervise_typed_worker(
            process,
            node,
            source,
            "aisle.nodes.segmented_pose",
            tmp_path / "evidence",
            outputs={"result"},
            timeout_s=5,
        )
    finally:
        log.close()
    assert result["ok"] is True, result
    assert result["classification"] == "module_result"
    assert result["input_exhausted"] is True
    assert result["rc"] == 0
    assert result["requests"] == 3
    assert [row[0] for row in raw.sent] == ["result", "turn_done"]
    assert json.loads((tmp_path / "evidence/worker.json").read_text()) == result
    frames = list((tmp_path / "evidence").glob("*.frame"))
    assert len(frames) == result["messages"]


@pytest.mark.parametrize("failure", ["receipt", "completion", "timeout", "truncated"])
def test_supervisor_refuses_forged_or_incomplete_worker_and_reaps_it(tmp_path, failure):
    """MON-13: worker claims and stalled/truncated output cannot attest host completion."""
    from aisle.harness.typed_node_supervisor import supervise_typed_worker

    script = """
import sys, time
from aisle.monolith.wire import receive, send
request = receive(sys.stdin.buffer)
receipt = {"module": request["module"], "source_sha256": request["source_sha256"]}
"""
    if failure == "receipt":
        script += 'send(sys.stdout.buffer, {"kind":"ready", **receipt, "source_sha256":"wrong"})\n'
    elif failure == "completion":
        script += """
send(sys.stdout.buffer, {"kind":"ready", **receipt})
send(sys.stdout.buffer, {"kind":"finished", **receipt, "node_created":True, "input_exhausted":True})
"""
    elif failure == "timeout":
        script += "time.sleep(60)\n"
    else:
        script += 'sys.stdout.buffer.write(b"\\x00\\x00"); sys.stdout.buffer.flush()\n'
    raw, node = _node()
    process, log = _spawn(tmp_path, script)
    try:
        result = supervise_typed_worker(
            process,
            node,
            "pass",
            "aisle.nodes.segmented_pose",
            tmp_path / "evidence",
            outputs={"result"},
            timeout_s=0.5,
        )
    finally:
        log.close()
    assert result["classification"] == "infrastructure_exclusion", result
    assert result["ok"] is False
    assert result["error"]
    assert process.poll() is not None
    assert not raw.sent
    assert (tmp_path / "evidence/worker.json").exists()


def test_supervisor_refuses_output_after_worker_terminal_message(tmp_path):
    """MON-13: a valid terminal envelope cannot conceal trailing protocol bytes."""
    from aisle.harness.typed_node_supervisor import supervise_typed_worker

    script = """
import sys
from aisle.monolith.wire import receive, send
request = receive(sys.stdin.buffer)
identity = {"module": request["module"], "source_sha256": request["source_sha256"]}
send(sys.stdout.buffer, {"kind":"ready", **identity})
send(sys.stdout.buffer, {
    "kind":"finished", **identity, "node_created":False, "input_exhausted":False
})
sys.stdout.buffer.write(b"trailing"); sys.stdout.buffer.flush()
"""
    raw, node = _node()
    process, log = _spawn(tmp_path, script)
    try:
        result = supervise_typed_worker(
            process,
            node,
            "pass",
            "aisle.nodes.segmented_pose",
            tmp_path / "evidence",
            outputs={"result"},
            timeout_s=5,
        )
    finally:
        log.close()
    assert result["classification"] == "infrastructure_exclusion", result
    assert "trailing" in result["error"]
    assert process.poll() is not None


@pytest.mark.parametrize("source", ["raise ValueError('policy failed')", "pass"])
def test_supervisor_preserves_ordinary_failure_or_early_exit(tmp_path, source):
    """MON-12: valid module failure/early exit remains distinct from infrastructure failure."""
    from aisle.harness.typed_node_supervisor import supervise_typed_worker

    raw, node = _node()
    process, log = _spawn(tmp_path)
    try:
        result = supervise_typed_worker(
            process,
            node,
            source,
            "aisle.nodes.segmented_pose",
            tmp_path / "evidence",
            outputs={"result"},
            timeout_s=5,
        )
    finally:
        log.close()
    assert result["classification"] == "module_result", result
    assert result["ok"] is False
    assert result["input_exhausted"] is False
    assert not raw.sent


def test_supervisor_reaps_adopted_child_on_cancellation(tmp_path, monkeypatch):
    """MON-12/MON-13: cancellation propagates only after child cleanup and evidence retention."""
    from aisle.harness import typed_node_supervisor

    raw, node = _node()
    process, log = _spawn(tmp_path)

    def interrupt(*args):
        raise KeyboardInterrupt("fixture cancellation")

    monkeypatch.setattr(typed_node_supervisor._DeadlinePipe, "read", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt, match="fixture cancellation"):
            typed_node_supervisor.supervise_typed_worker(
                process,
                node,
                "pass",
                "aisle.nodes.segmented_pose",
                tmp_path / "evidence",
                outputs={"result"},
                timeout_s=5,
            )
    finally:
        log.close()
    assert process.poll() is not None
    result = json.loads((tmp_path / "evidence/worker.json").read_text())
    assert result["classification"] == "infrastructure_exclusion"
    assert result["error"] == "fixture cancellation"
    assert not raw.sent


@pytest.mark.parametrize("failure", ["output_type", "occupied_output", "budget"])
def test_invalid_setup_still_reaps_the_adopted_worker(tmp_path, failure):
    """MON-12/MON-13: setup refusal reaps the worker and preserves existing evidence."""
    from aisle.harness.typed_node_supervisor import supervise_typed_worker

    raw, node = _node()
    output = tmp_path / "evidence"
    timeout = 5
    if failure == "output_type":
        output = None
    elif failure == "occupied_output":
        output.mkdir()
        (output / "sentinel").write_text("retain")
    else:
        timeout = False
    process, log = _spawn(tmp_path, "import time; time.sleep(60)")
    try:
        result = supervise_typed_worker(
            process,
            node,
            "pass",
            "aisle.nodes.segmented_pose",
            output,
            outputs={"result"},
            timeout_s=timeout,
        )
    finally:
        log.close()
    assert result["classification"] == "infrastructure_exclusion"
    assert result["error"]
    assert process.poll() is not None
    assert process.stdin.closed and process.stdout.closed
    assert not raw.sent
    if failure == "occupied_output":
        assert (output / "sentinel").read_text() == "retain"
        assert not (output / "worker.json").exists()
