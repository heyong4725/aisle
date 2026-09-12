"""MON-8/MON-12/MON-13: hosted refusals bind the actual retained provider request."""

import hashlib
import json

import pytest
from test_frontend_dispatch import call
from test_hosted_dispatch import REQUEST

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit


def _case(tmp_path):
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        budget.dispatch(call(1), b"local", lambda _: None)
        with pytest.raises(DispatchRefused):
            budget.hosted_response("00000001", REQUEST, lambda _: pytest.fail("sent upstream"))
    artifacts = {
        "invocation.json": json.dumps(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": {
                    "base_url": "http://127.0.0.1:1/v1",
                    "hosted_tool_contract": "aisle.fixture.responses.max_tool_calls.v1",
                },
                "delegated_tools": [],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ).encode(),
        "00000001-request.body": REQUEST,
        "00000001-request.json": b'{"content_encoding":"identity","authorization_present":false}',
        "00000001-delivery.json": (
            b'{"sequence":null,"events_returned":0,"error_type":"DispatchRefused"}'
        ),
    }
    dispatch = {
        "artifacts": {p.name: p.read_bytes() for p in output.iterdir()},
        "expected": budget.reference(),
        "byte_limit": 100000,
    }
    return artifacts, dispatch


@pytest.mark.parametrize(
    "drift", [None, "request", "encoding", "delivery", "response", "session", "contract", "error"]
)
def test_refusal_source_requires_exact_owned_exchange(tmp_path, drift):
    """MON-13: rehashed unrelated requests, errors, or returned data cannot qualify a refusal."""
    from aisle.harness.provider_source_audit import link_hosted_refusal_sources

    artifacts, dispatch = _case(tmp_path)
    if drift == "request":
        value = json.loads(REQUEST)
        value["model"] = "different"
        artifacts["00000001-request.body"] = json.dumps(value).encode()
    elif drift == "encoding":
        artifacts["00000001-request.json"] = (
            b'{"content_encoding":"gzip","authorization_present":false}'
        )
    elif drift == "delivery":
        artifacts["00000001-delivery.json"] = (
            b'{"sequence":1,"events_returned":1,"error_type":"DispatchRefused"}'
        )
    elif drift == "response":
        artifacts["00000001-response-partial.sse"] = b"unexpected upstream bytes"
    elif drift in {"session", "contract"}:
        invocation = json.loads(artifacts["invocation.json"])
        if drift == "session":
            invocation["session_id"] = "different"
        else:
            del invocation["binding"]["hosted_tool_contract"]
        artifacts["invocation.json"] = json.dumps(invocation).encode()
    expected = {
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
        "bytes": sum(map(len, artifacts.values())),
        "failure": "TimeoutError" if drift == "error" else "DispatchRefused",
        "complete_coverage": False,
        "confinement_verified": False,
    }
    report = link_hosted_refusal_sources(
        artifacts, expected=expected, dispatch=dispatch, byte_limit=100000
    )
    assert report["ok"] is (drift is None), report
    assert report["complete_coverage"] is False
    if drift is None:
        assert report["linked_requests"] == ["00000001"]
    else:
        assert report["linked_requests"] == []
