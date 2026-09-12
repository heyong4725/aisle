"""MON-12/MON-13: native refusal replay withholds the exact executable provider event."""

import hashlib
import json

import pytest
from test_provider_response_authority import frames, item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "count",
        "source",
        "failure",
        "delegation",
        "hash",
        "trailing_delegation",
        "cancelled",
        "cancelled_count",
        "cancelled_sequence",
        "cancelled_error",
        "cancelled_source",
        "cancelled_hosted",
    ],
)
def test_native_refusal_replays_exact_delivery_prefix(tmp_path, drift):
    """MON-13: substituted calls, delegated bypasses and false delivery counts fail closed."""
    from aisle.harness.provider_source_audit import link_native_refusal_sources

    trailing = drift == "trailing_delegation"
    delegated = {("harness", "check", "function_call")} if trailing else set()
    source = frames(
        [item(1), item(2)] + ([item(3, namespace="harness", name="check")] if trailing else [])
    )
    delivered = []
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        with pytest.raises(DispatchRefused):
            ProviderResponseAuthority(budget).forward(source, delivered.append)
    artifacts = {
        "invocation.json": json.dumps(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": {"base_url": "http://127.0.0.1:1/v1"},
                "delegated_tools": list(delegated),
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ).encode(),
        "00000001-request.body": b"{}",
        "00000001-request.json": b'{"content_encoding":"identity","authorization_present":false}',
        "00000001-response.sse": frames([item(1), item(3)]) if drift == "source" else source,
        "00000001-delivery.json": json.dumps(
            {
                "sequence": 1,
                "events_returned": len(delivered) + (drift == "count"),
                "error_type": "DispatchRefused",
            }
        ).encode(),
    }
    if drift and drift.startswith("cancelled"):
        cancelled_source = frames([item(9)], response_id="cancelled")
        if drift == "cancelled_hosted":
            cancelled_source = frames(
                [{"type": "web_search_call", "id": "search", "status": "completed"}],
                response_id="cancelled",
            )
        artifacts.update(
            {
                "00000002-request.body": b"{}",
                "00000002-request.json": (
                    b'{"content_encoding":"identity","authorization_present":false}'
                ),
                "00000002-response.sse": b"invalid"
                if drift == "cancelled_source"
                else cancelled_source,
                "00000002-delivery.json": json.dumps(
                    {
                        "sequence": 2 if drift == "cancelled_sequence" else None,
                        "events_returned": 1 if drift == "cancelled_count" else 0,
                        "error_type": "TimeoutError"
                        if drift == "cancelled_error"
                        else "ValueError",
                    }
                ).encode(),
            }
        )
    reference = {
        "artifacts": {n: hashlib.sha256(v).hexdigest() for n, v in artifacts.items()},
        "bytes": sum(map(len, artifacts.values())),
        "failure": "TimeoutError" if drift == "failure" else "DispatchRefused",
        "complete_coverage": False,
        "confinement_verified": False,
    }
    if drift == "hash":
        artifacts["00000001-request.body"] = b"[]"
    report = link_native_refusal_sources(
        artifacts,
        expected=reference,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in output.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 100000,
        },
        byte_limit=100000,
        delegated_tools={(None, "exec_command", "function_call")}
        if drift == "delegation"
        else delegated,
    )
    passed = drift in {None, "cancelled"} or trailing
    assert report["ok"] is passed, report
    assert report["complete_coverage"] is False
    if passed:
        assert report["refused_attempts"] == [2]
        assert report["delegated_calls"] == []
        assert report["withheld_requests"] == (["00000002"] if drift == "cancelled" else [])
    else:
        assert report["refused_attempts"] == []
