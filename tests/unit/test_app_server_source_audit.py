"""MON-12/MON-13: grants must match requests from the retained owned frontend pipe."""

import hashlib
import json

import pytest

pytestmark = pytest.mark.unit


def _case():
    call = {
        "id": 7,
        "method": "item/tool/call",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "call",
            "namespace": "harness",
            "tool": "check",
            "arguments": {},
        },
    }
    received = [
        {"id": "initialize", "result": {}},
        {"id": "thread", "result": {"thread": {"id": "thread"}}},
        {"id": "turn", "result": {"turn": {"id": "turn"}}},
        call,
        {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
        },
    ]
    sent = [
        {
            "id": "initialize",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "aisle", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "initialized"},
        {"id": "thread", "method": "thread/start", "params": {}},
        {"id": "turn", "method": "turn/start", "params": {"threadId": "thread", "input": []}},
        {"id": 7, "result": {"success": True, "contentItems": []}},
    ]
    records = {
        "invocation.json": {
            "thread_params": {},
            "input_items": [],
            "transport": "owned_stdio",
            "complete_coverage": False,
            "confinement_verified": False,
        }
    }
    for direction, rows in (("received", received), ("sent", sent)):
        records.update(
            {f"{number:08d}-{direction}.json": row for number, row in enumerate(rows, 1)}
        )
    grants = [{"call": {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}}]
    return records, grants


def _audit(records, grants, **kwargs):
    from aisle.harness.frontend_app_server_audit import verify_app_server_sources

    artifacts = {name: json.dumps(row).encode() + b"\n" for name, row in records.items()}
    return verify_app_server_sources(
        artifacts,
        expected={
            "thread_id": "thread",
            "turn_id": "turn",
            "dynamic_calls": 1,
            "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
        },
        grants=grants,
        byte_limit=65536,
        **kwargs,
    )


def test_grant_identity_is_recomputed_from_owned_pipe_source():
    """MON-12: a normalized grant alone cannot prove which frontend call requested it."""
    records, grants = _case()
    result = _audit(records, grants)
    assert result["ok"], result
    assert result["linked_calls"] == 1
    assert result["complete_coverage"] is False


@pytest.mark.parametrize(
    "drift", ["call", "thread", "missing", "duplicate", "reply", "prompt", "terminal"]
)
def test_source_chain_rejects_semantic_drift_even_when_hashes_match(drift):
    """MON-13: changed, absent or repeated source calls and replies invalidate the chain."""
    records, grants = _case()
    if drift == "call":
        grants[0]["call"]["call_id"] = "invented"
    elif drift == "thread":
        records["00000004-received.json"]["params"]["threadId"] = "foreign"
    elif drift == "missing":
        del records["00000004-received.json"]
    elif drift == "duplicate":
        records["00000006-received.json"] = records["00000004-received.json"]
    elif drift == "reply":
        records["00000005-sent.json"]["id"] = 8
    elif drift == "prompt":
        records["00000003-sent.json"]["params"] = {"baseInstructions": "changed"}
    else:
        records["00000005-received.json"]["params"]["turn"]["status"] = "failed"
    result = _audit(records, grants)
    assert not result["ok"], result


@pytest.mark.parametrize(
    "drift", [None, "frame", "call", "ceiling", "missing", "uncertain", "extra"]
)
def test_reservations_bind_exact_source_and_admitted_ceiling(tmp_path, drift):
    """MON-8/MON-13: consistent separate journals cannot substitute another source or budget."""
    from aisle.harness.frontend_dispatch import DispatchBudget

    records, grants = _case()
    grants[0]["session_id"] = "session"
    frame = json.dumps(records["00000004-received.json"]).encode() + b"\n"
    call = dict(grants[0]["call"])
    if drift == "frame":
        frame = frame[:-1] + b" \n"  # same normalized call; different acquired bytes
    if drift == "call":
        call["call_id"] = "other"
    output = tmp_path / "dispatch"
    with DispatchBudget(
        output, session_id="session", ceiling=2 if drift in {"ceiling", "extra"} else 1
    ) as budget:

        def deliver(raw):
            if drift == "uncertain":
                raise OSError("uncertain delivery")

        if drift == "uncertain":
            with pytest.raises(OSError):
                budget.dispatch(call, frame, deliver)
        else:
            budget.dispatch(call, frame, deliver)
        if drift == "extra":
            budget.dispatch({**call, "call_id": "extra"}, frame, deliver)
    artifacts = {path.name: path.read_bytes() for path in output.iterdir()}
    if drift == "missing":
        del artifacts["00000001.frame"]
    report = _audit(
        records,
        grants,
        dispatch={"artifacts": artifacts, "expected": budget.reference(), "byte_limit": 65536},
        dispatch_ceiling=1,
    )
    assert report["ok"] is (drift is None), report
    if drift is None:
        assert report["reservations_verified"] is True
        assert report["linked_calls"] == 1
        assert report["complete_coverage"] is False


def test_admitted_reservation_requirement_cannot_downgrade_to_source_only():
    """MON-8/MON-13: valid source/grant evidence cannot waive the admitted reservation gate."""
    records, grants = _case()
    report = _audit(records, grants, dispatch_ceiling=1)
    assert not report["ok"], report
    assert report["reservations_verified"] is False
