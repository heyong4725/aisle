"""MON-12/MON-13: hosted refusal must replay earlier work against the original closed snapshot."""

import copy
import hashlib
import json

import pytest
from test_hosted_dispatch import REQUEST, hosted_frame
from test_provider_response_authority import frames, item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


def encode(value):
    return json.dumps(value).encode()


@pytest.mark.parametrize(
    "drift", [None, "native", "hosted", "request", "after_refusal", "extra", "refused_response"]
)
def test_hosted_refusal_replays_prior_native_and_hosted_work(tmp_path, drift):
    """MON-13: no sliced or reissued closed reference can conceal changed earlier work."""
    from aisle.harness.provider_source_audit import verify_hosted_refusal_prefix

    first, second = frames([item(1)]), hosted_frame(1, response_id="hosted-response")
    returned_first, returned_second = [], []
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=2) as budget:
        authority = ProviderResponseAuthority(budget)
        authority.forward(first, returned_first.append)
        budget.hosted_response("00000002", REQUEST, lambda _: second)
        authority.forward(second, returned_second.append)
        with pytest.raises(DispatchRefused):
            budget.hosted_response("00000003", REQUEST, lambda _: pytest.fail("sent upstream"))
    if drift == "native":
        first = frames([item(99)])
    elif drift == "hosted":
        second = hosted_frame(0, response_id="hosted-response")
    artifacts = {
        "invocation.json": encode(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": {
                    "base_url": "http://127.0.0.1:1234/v1",
                    "hosted_tool_contract": "aisle.fixture.responses.max_tool_calls.v1",
                },
                "delegated_tools": [],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        )
    }
    for number, request, source, count in (
        (1, b'{"stream":true,"tools":[]}', first, len(returned_first)),
        (2, REQUEST, second, len(returned_second)),
        (3, REQUEST, None, 0),
    ):
        artifacts.update(
            {
                f"{number:08d}-request.body": request,
                f"{number:08d}-request.json": encode(
                    {"content_encoding": "identity", "authorization_present": False}
                ),
                f"{number:08d}-delivery.json": encode(
                    {
                        "sequence": number if source is not None else None,
                        "events_returned": count,
                        "error_type": None if source is not None else "DispatchRefused",
                    }
                ),
            }
        )
        if source is not None:
            artifacts[f"{number:08d}-response.sse"] = source
    if drift == "request":
        request = json.loads(REQUEST)
        request["model"] = "other"
        artifacts["00000002-request.body"] = encode(request)
    elif drift == "after_refusal":
        artifacts.update(
            {
                "00000004-request.body": b'{"tools":[]}',
                "00000004-request.json": encode(
                    {"content_encoding": "identity", "authorization_present": False}
                ),
                "00000004-response.sse": frames([], response_id="after"),
                "00000004-delivery.json": encode(
                    {"sequence": 3, "events_returned": 2, "error_type": None}
                ),
            }
        )
    elif drift == "extra":
        artifacts["unindexed.bin"] = b"extra"
    elif drift == "refused_response":
        artifacts["00000003-response.sse"] = b"upstream work happened"
    expected = {
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
        "bytes": sum(map(len, artifacts.values())),
        "failure": "DispatchRefused",
        "complete_coverage": False,
        "confinement_verified": False,
    }
    before = copy.deepcopy(expected)
    report = verify_hosted_refusal_prefix(
        artifacts,
        expected=expected,
        delegated_tools=(),
        byte_limit=100000,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in output.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 100000,
        },
    )
    assert expected == before
    assert report["ok"] is (drift is None), report
    if drift is None:
        assert report["native_attempts"] == [1]
        assert report["linked_requests"] == ["00000003"]
        assert len(report["hosted_calls"]) == 1
    assert report["complete_coverage"] is False
