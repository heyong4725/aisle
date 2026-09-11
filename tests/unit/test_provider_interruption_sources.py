"""MON-12/MON-13: interrupted source delivery retains its exact charged boundary."""

import asyncio
import hashlib
import json

import pytest
from test_provider_response_authority import frames, item

from aisle.harness import provider_source_audit
from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("forwarded", [False, True])
@pytest.mark.parametrize("drift", [None, "phase", "cause", "count", "attempt"])
@pytest.mark.parametrize("hosted", [False, "unused", "active"])
def test_interrupted_provider_delivery_keeps_exact_prefix(tmp_path, forwarded, drift, hosted):
    """MON-13: the same closed source cannot claim a different delivery boundary or cause."""
    root = tmp_path / "dispatch"
    items = [item(1)]
    if hosted == "active":
        items.append(
            {
                "type": "web_search_call",
                "id": "search",
                "status": "completed",
                "action": {"type": "search", "query": "fixture"},
            }
        )
    source = frames(items)
    request = (
        json.dumps({"stream": True, "tools": [{"type": "web_search"}]}).encode()
        if hosted
        else b"{}"
    )
    binding = {"base_url": "http://127.0.0.1:1/v1"}
    if hosted:
        binding["hosted_tool_contract"] = "aisle.fixture.responses.max_tool_calls.v1"
    delivered = []
    with DispatchBudget(root, session_id="session", ceiling=3) as budget:
        if hosted:
            budget.hosted_response("00000001", request, lambda _: source)
        authority = ProviderResponseAuthority(budget)
        original = budget.dispatch

        def interrupted(call, frame, deliver):
            def fail(raw):
                if forwarded:
                    deliver(raw)
                raise asyncio.CancelledError("injected")

            return original(call, frame, fail)

        budget.dispatch = interrupted
        with pytest.raises(asyncio.CancelledError):
            authority.forward(source, delivered.append)
        with pytest.raises(DispatchRefused, match="closed"):
            original(
                {"turn_id": "new", "call_id": "new", "tool_name": "exec_command"},
                b"new",
                delivered.append,
            )
    artifacts = {
        "invocation.json": json.dumps(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": binding,
                "delegated_tools": [],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ).encode(),
        "00000001-request.body": request,
        "00000001-request.json": b'{"content_encoding":"identity","authorization_present":false}',
        "00000001-response.sse": source,
        "00000001-delivery.json": json.dumps(
            {
                "sequence": 1,
                "events_returned": len(delivered) + int(drift == "count"),
                "error_type": "ValueError" if drift == "cause" else "CancelledError",
            }
        ).encode(),
    }
    expected = {
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
        "bytes": sum(map(len, artifacts.values())),
        "failure": "CancelledError",
        "complete_coverage": False,
        "confinement_verified": False,
    }
    kwargs = dict(
        expected=expected,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in root.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 65536,
        },
        byte_limit=65536,
        delegated_tools=frozenset(),
    )
    report = provider_source_audit.verify_provider_interruption_sources(
        artifacts,
        **kwargs,
        failed_attempt=2 if drift == "attempt" else 1,
        forwarded=not forwarded if drift == "phase" else forwarded,
    )
    assert report["ok"] is (drift is None and hosted != "active"), report
    if drift is None and hosted != "active":
        assert report["interrupted_attempts"] == [1]
        assert report["native_attempts"] == [1]
        assert not report["complete_coverage"] and not report["confinement_verified"]
        assert not provider_source_audit.verify_provider_sources(artifacts, **kwargs)["ok"]
