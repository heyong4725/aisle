"""MON-8/MON-12/MON-13: parent-owned worker lifecycle and evidence."""

import hashlib
import json
from pathlib import Path

import pytest
from test_monolith_worker import _worker

pytestmark = pytest.mark.unit


def test_supervisor_runs_expert_and_seals_rpc_evidence(tmp_path):
    """MON-12: an actual worker session retains source binding, messages and terminal status."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.supervisor import WorkerSupervisor

    source = (Path(__file__).resolve().parents[2] / "experts/monolithic/expert_t1.py").read_text()
    output = tmp_path / "evidence"
    with _worker(tmp_path) as (process, _, _):
        with WorkerSupervisor(process, Primitives._load("franka"), output, timeout_s=5) as worker:
            receipt = worker.initialize(source, "expert_t1.py")
            assert receipt["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
            event = {"name": "tick", "payload": 1, "sim_time_ns": 1_000_000_000, "goal_id": ""}
            assert worker.event(event) == []
        assert process.poll() == 0
    record = json.loads((output / "worker.json").read_text())
    assert record["state"] == "closed" and record["error"] is None
    assert record["primitive_calls"] >= 2
    events = [json.loads(line) for line in (output / "messages.jsonl").read_text().splitlines()]
    assert {row["direction"] for row in events} == {"parent", "worker"}
    for row in events:
        raw = (output / row["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        assert row["bytes"] == len(raw)


def test_supervisor_stops_hung_constructor(tmp_path):
    """MON-8/MON-13: the pipe deadline kills and reaps a nonreplying authored constructor."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.supervisor import WorkerFailure, WorkerSupervisor

    source = (
        "API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log):\n  while True: pass\n"
    )
    with _worker(tmp_path) as (process, _, _):
        worker = WorkerSupervisor(
            process, Primitives._load("franka"), tmp_path / "evidence", timeout_s=0.3
        )
        with pytest.raises(WorkerFailure, match="deadline"):
            worker.initialize(source, "hang.py")
        assert process.poll() is not None
    record = json.loads((tmp_path / "evidence/worker.json").read_text())
    assert record["state"] == "failed"
    assert "deadline" in record["error"]


def test_supervisor_refuses_invalid_actions_before_returning_them(tmp_path):
    """MON-4/MON-13: worker actions pass the actual broker gate before reaching the caller."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.supervisor import WorkerFailure, WorkerSupervisor

    source = """API_VERSION='1.0'
class Controller:
 def __init__(self,p,log): pass
 def on_event(self,event): return [{'gripper_cmd': float('nan')}]
"""
    with _worker(tmp_path) as (process, _, _):
        worker = WorkerSupervisor(
            process, Primitives._load("franka"), tmp_path / "evidence", timeout_s=5
        )
        worker.initialize(source, "invalid-action.py")
        with pytest.raises(WorkerFailure, match="finite"):
            worker.event({"name": "tick", "payload": 1})
        assert process.poll() is not None


def test_supervisor_keeps_truncated_worker_bytes(tmp_path):
    """MON-12/MON-13: malformed partial frames survive protocol failure as raw evidence."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.supervisor import WorkerFailure, WorkerSupervisor

    program = (
        "import os, sys; from aisle.monolith.wire import receive; "
        "receive(sys.stdin.buffer); os.write(1, b'broken')"
    )
    with _worker(tmp_path, program=program) as (process, _, _):
        worker = WorkerSupervisor(
            process, Primitives._load("franka"), tmp_path / "evidence", timeout_s=5
        )
        with pytest.raises(WorkerFailure):
            worker.initialize("", "fixture.py")
        assert process.poll() is not None
    rows = [
        json.loads(row) for row in (tmp_path / "evidence/messages.jsonl").read_text().splitlines()
    ]
    incoming = [row for row in rows if row["direction"] == "worker"]
    assert incoming and incoming[-1]["complete"] is False
    assert (tmp_path / "evidence" / incoming[-1]["file"]).read_bytes() == b"broken"


def test_denied_primitive_request_stops_worker_even_if_module_would_catch_it(tmp_path):
    """MON-8/MON-13: denied authority is terminal at the parent boundary, not module discretion."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.supervisor import WorkerFailure, WorkerSupervisor

    source = """API_VERSION='1.0'
class Controller:
 def __init__(self,p,log):
  try:
   p._client._exchange({'op':'get','handle':0,'name':'__dict__','args':[],'kwargs':{}})
  except Exception:
   pass
 def on_event(self,event): return []
"""
    with _worker(tmp_path) as (process, _, _):
        worker = WorkerSupervisor(
            process, Primitives._load("franka"), tmp_path / "evidence", timeout_s=5
        )
        with pytest.raises(WorkerFailure, match="primitive request refused"):
            worker.initialize(source, "forbidden.py")
        assert process.poll() is not None
        with pytest.raises(WorkerFailure):
            worker.event({"name": "tick", "payload": 1})


def test_supervisor_owns_child_cleanup_on_invalid_configuration(tmp_path):
    """MON-12: adopting a child cannot leak it when primitive budget setup fails."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequestError
    from aisle.monolith.supervisor import WorkerSupervisor

    with _worker(tmp_path) as (process, _, _):
        with pytest.raises(PrimitiveRequestError):
            WorkerSupervisor(
                process,
                Primitives._load("franka"),
                tmp_path / "evidence",
                timeout_s=5,
                max_primitive_calls=0,
            )
        assert process.poll() is not None
