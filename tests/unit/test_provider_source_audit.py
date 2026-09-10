"""MON-12/MON-13: delegated provider calls retain the frontend-to-controller link."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _audit, _case
from test_provider_response_authority import frames, item

from aisle.harness.frontend_app_server import dynamic_tools
from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("drift", [None, "call_id", "arguments", "missing"])
def test_provider_delegation_must_reach_the_exact_frontend_source(tmp_path, drift):
    """MON-13: hash-consistent provider records cannot stand in for another source call."""
    records, grants = _case()
    thread = {"dynamicTools": dynamic_tools(["check"])}
    records["invocation.json"]["thread_params"] = thread
    records["00000003-sent.json"]["params"] = thread
    grants[0]["session_id"] = "session"
    dispatch_root = tmp_path / "dispatch"
    with DispatchBudget(dispatch_root, session_id="session", ceiling=1) as budget:
        budget.dispatch(
            grants[0]["call"],
            json.dumps(records["00000004-received.json"]).encode() + b"\n",
            lambda _: None,
        )
    call = item(1, namespace="harness", name="check")
    call["call_id"] = "other" if drift == "call_id" else "call"
    if drift == "arguments":
        call["arguments"] = '{"unexpected":true}'
    source = frames([] if drift == "missing" else [call])
    artifacts = {
        "invocation.json": json.dumps(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": {"base_url": "http://127.0.0.1:1/v1"},
                "delegated_tools": [["harness", "check", "function_call"]],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ).encode(),
        "00000001-request.body": b"{}",
        "00000001-request.json": b'{"content_encoding":"identity","authorization_present":false}',
        "00000001-response.sse": source,
        "00000001-delivery.json": json.dumps(
            {
                "sequence": 1,
                "events_returned": 2 if drift == "missing" else 3,
                "error_type": None,
            }
        ).encode(),
    }
    provider = {
        "artifacts": artifacts,
        "byte_limit": 65536,
        "expected": {
            "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
            "bytes": sum(map(len, artifacts.values())),
            "failure": None,
            "complete_coverage": False,
            "confinement_verified": False,
        },
    }
    report = _audit(
        records,
        grants,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in dispatch_root.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 65536,
        },
        dispatch_ceiling=1,
        provider=provider,
    )
    assert report["ok"] is (drift is None), report
