"""MON-12/MON-13: a later refusal cannot hide changed or unmatched earlier provider work."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case
from test_provider_response_authority import frames, item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.frontend_qualification import refusal_source_evidence
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


def encode(value):
    return json.dumps(value).encode() + b"\n"


@pytest.mark.parametrize("drift", [None, "native", "unmatched", "delegated", "binding", "missing"])
def test_refused_harness_checks_entire_provider_prefix(tmp_path, drift):
    """MON-13: replay earlier native reservations and match delegated requests to the owned pipe."""
    records, _ = _case()
    del records["00000005-received.json"], records["00000005-sent.json"]
    records["failure.json"] = {
        "error_type": "DispatchRefused",
        "error": "dispatch budget exhausted",
    }
    protocol = {name: encode(value) for name, value in records.items()}
    protocol_ref = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 1,
        "stream_complete": False,
        "failure": records["failure.json"],
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in protocol.items()},
    }
    delegated = {("harness", "check", "function_call")}
    first = frames([item(1)])
    harness = {
        "id": "harness-item",
        "type": "function_call",
        "call_id": "call",
        "namespace": "harness",
        "name": "check",
        "arguments": "{}",
    }
    second = frames([harness], response_id="harness-response")
    ceiling = 2 if drift == "unmatched" else 1
    root = tmp_path / "dispatch"
    returned = []
    with DispatchBudget(root, session_id="session", ceiling=ceiling) as budget:
        authority = ProviderResponseAuthority(budget, delegated_tools=delegated)
        authority.forward(first, returned.append)
        if drift == "unmatched":
            budget.dispatch(
                {
                    "turn_id": "foreign",
                    "call_id": "extra",
                    "tool_name": '[null,"exec_command","function_call"]',
                },
                b"not in provider",
                lambda _: None,
            )
        with pytest.raises(DispatchRefused):
            budget.dispatch(
                {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"},
                protocol["00000004-received.json"],
                lambda _: pytest.fail("dispatched"),
            )
    if drift == "native":
        first = frames([item(99)])
    elif drift == "delegated":
        harness["arguments"] = '{"foreign":true}'
        second = frames([harness], response_id="harness-response")
    binding = {"base_url": "http://127.0.0.1:1234/v1"}
    provider = {
        "invocation.json": encode(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": binding,
                "delegated_tools": [list(x) for x in delegated],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        )
    }
    for index, response in enumerate((first, second), 1):
        provider.update(
            {
                f"{index:08d}-request.body": b"{}",
                f"{index:08d}-request.json": encode(
                    {"content_encoding": "identity", "authorization_present": False}
                ),
                f"{index:08d}-response.sse": response,
                f"{index:08d}-delivery.json": encode(
                    {"sequence": index, "events_returned": len(returned), "error_type": None}
                ),
            }
        )
    reference = {
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in provider.items()},
        "bytes": sum(map(len, provider.values())),
        "failure": None,
        "complete_coverage": False,
        "confinement_verified": False,
    }
    artifacts = {"frontend-protocol/" + name: raw for name, raw in protocol.items()}
    artifacts.update({"frontend-dispatch/" + p.name: p.read_bytes() for p in root.iterdir()})
    artifacts.update({"provider/" + name: raw for name, raw in provider.items()})
    artifacts.update(
        {
            "frontend-protocol-reference.json": encode(protocol_ref),
            "frontend-dispatch-reference.json": encode(budget.reference()),
            "frontend-authority-reference.json": encode({"session_id": "session", "artifacts": {}}),
            "provider-reference.json": encode(reference),
        }
    )
    if drift == "missing":
        del artifacts["provider-reference.json"]
    proof = {
        "artifacts": artifacts,
        "record": {"arm": "typed", "plan_id": "plan", "session_id": "session"},
        "admission": {
            "immutable_id": "plan",
            "arms": {
                "typed": {
                    "budget": {"frontend_tool_ceiling": ceiling},
                    "policy": {"allowed_external_tools": ["harness.check"]},
                }
            },
            "launch_bindings": {
                "typed": {
                    "app_server": {},
                    "provider": {"base_url": "http://127.0.0.1:9999/v1"}
                    if drift == "binding"
                    else binding,
                }
            },
        },
    }
    if drift is None:
        report = refusal_source_evidence(proof)
        assert report["source_audits"]["provider_prefix"]["native_attempts"] == [1]
    else:
        with pytest.raises(ValueError):
            refusal_source_evidence(proof)
