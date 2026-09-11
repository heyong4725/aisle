"""MON-13: authenticate replay rejection without discarding the original source closure."""

import hashlib
import json

import pytest
from test_provider_response_authority import frames, item

from aisle.harness import provider_source_audit
from aisle.harness.frontend_dispatch import DispatchBudget
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("identity", ["response", "call"])
@pytest.mark.parametrize("drift", [None, "fresh", "forwarded", "cause", "continued", "malformed"])
def test_replay_failure_requires_exact_terminal_identity_rejection(tmp_path, identity, drift):
    """MON-13: an unrelated failure or released replay cannot qualify as replay refusal."""
    source = frames([item(1)])
    repeated = frames(
        [item(1)], response_id="response-1" if identity == "response" else "response-2"
    )
    root = tmp_path / "dispatch"
    with DispatchBudget(root, session_id="session", ceiling=3) as budget:
        authority = ProviderResponseAuthority(budget)
        delivered = []
        authority.forward(source, delivered.append)
        with pytest.raises(ValueError, match="replay"):
            authority.forward(repeated, lambda _: pytest.fail("replay was delivered"))
    artifacts = {
        "invocation.json": json.dumps(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": {"base_url": "http://127.0.0.1:1/v1"},
                "delegated_tools": [],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ).encode()
    }
    if drift == "fresh":
        repeated = frames([item(2)], response_id="response-2")
    if drift == "malformed":
        repeated = b"invalid SSE"
    for number, raw in enumerate([source, repeated], 1):
        prefix = f"{number:08d}"
        artifacts[prefix + "-request.body"] = b"{}"
        artifacts[prefix + "-request.json"] = (
            b'{"content_encoding":"identity","authorization_present":false}'
        )
        artifacts[prefix + "-response.sse"] = raw
        artifacts[prefix + "-delivery.json"] = json.dumps(
            {
                "sequence": number,
                "events_returned": len(delivered) if number == 1 else int(drift == "forwarded"),
                "error_type": None
                if number == 1
                else ("TimeoutError" if drift == "cause" else "ValueError"),
            }
        ).encode()
    if drift == "continued":
        for suffix in ("request.body", "request.json", "response.sse", "delivery.json"):
            artifacts["00000003-" + suffix] = artifacts["00000001-" + suffix]
        artifacts["00000003-delivery.json"] = (
            b'{"sequence":3,"events_returned":3,"error_type":null}'
        )
    expected = {
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
        "bytes": sum(map(len, artifacts.values())),
        "failure": "ValueError",
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
    report = provider_source_audit.verify_provider_replay_sources(artifacts, **kwargs)
    assert report["ok"] is (drift is None), report
    assert not report["complete_coverage"] and not report["confinement_verified"]
    if drift is None:
        assert report["native_attempts"] == [1]
        assert report["replayed_requests"] == ["00000002"]
        assert not provider_source_audit.verify_provider_sources(artifacts, **kwargs)["ok"]
