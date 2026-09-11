"""MON-12/MON-13: dynamic quota refusals bind closed, incomplete owned protocol evidence."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case
from test_frontend_dispatch import call

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "drift",
    [None, "thread", "reply", "missing_setup", "failure", "frame", "terminal", "initialize"],
)
def test_dynamic_refusal_needs_bound_unanswered_owned_call(tmp_path, drift):
    """MON-13: foreign calls, replies and incomplete identity setup cannot prove refusal."""
    from aisle.harness.frontend_app_server_audit import link_dynamic_refusal_sources

    records, _ = _case()
    del records["00000005-received.json"]
    reply = records.pop("00000005-sent.json")
    records["failure.json"] = {
        "error_type": "DispatchRefused",
        "error": "dispatch budget exhausted",
    }

    def encode(row):
        return json.dumps(row).encode() + b"\n"

    frame = encode(records["00000004-received.json"])
    identity = {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        budget.dispatch(call(1), b"earlier", lambda _: None)
        with pytest.raises(DispatchRefused):
            budget.dispatch(identity, frame, lambda _: pytest.fail("delivered"))
    if drift == "thread":
        records["00000004-received.json"]["params"]["threadId"] = "foreign"
    elif drift == "reply":
        records["00000005-sent.json"] = reply
    elif drift == "missing_setup":
        records["00000002-received.json"] = {"method": "notice", "params": {}}
    elif drift == "failure":
        records["failure.json"]["error_type"] = "TimeoutError"
    elif drift == "frame":
        records["00000004-received.json"]["id"] = 8
    elif drift == "terminal":
        records["00000005-received.json"] = {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
        }
    elif drift == "initialize":
        records["00000001-received.json"] = {"method": "notice", "params": {}}
    artifacts = {name: encode(row) for name, row in records.items()}
    expected = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 1,
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
        "stream_complete": False,
        "failure": records["failure.json"],
    }
    report = link_dynamic_refusal_sources(
        artifacts,
        expected=expected,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in output.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 100000,
        },
        byte_limit=100000,
    )
    assert report["ok"] is (drift is None), report
    assert report["complete_coverage"] is False
    assert report["refused_attempts"] == ([2] if drift is None else [])
