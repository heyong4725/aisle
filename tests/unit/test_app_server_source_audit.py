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


def _audit(records, grants):
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
