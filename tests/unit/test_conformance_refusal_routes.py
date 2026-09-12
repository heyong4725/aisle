"""MON-12/MON-13: each refusal source path participates in admitted-session composition."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case
from test_frontend_dispatch import call
from test_hosted_dispatch import REQUEST
from test_mcp_harness_source import frames as mcp_frames
from test_provider_response_authority import frames as provider_frames
from test_provider_response_authority import item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.frontend_qualification import refusal_source_evidence
from aisle.harness.mcp_harness_authority import MCPHarnessAuthority
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


def encode(row):
    return json.dumps(row).encode() + b"\n"


@pytest.mark.parametrize("route", ["hosted", "provider", "mcp"])
@pytest.mark.parametrize("enabled", [True, False])
def test_combined_refusal_requires_actual_source_route_admission(tmp_path, route, enabled):
    """MON-13: retained provider/MCP refusals cannot create an unadmitted launch route."""
    records, _ = _case()
    for name in ("00000004-received.json", "00000005-received.json", "00000005-sent.json"):
        del records[name]
    records["failure.json"] = {"error_type": "ValueError", "error": "source failed"}
    artifacts, launch = {}, {"app_server": {}}
    operations = ["harness.check"] if route == "mcp" else []
    mcp_count = 0
    root = tmp_path / "dispatch"
    with DispatchBudget(root, session_id="session", ceiling=1) as budget:
        if route == "provider":
            source = provider_frames([item(1), item(2)])
            delivered = []
            with pytest.raises(DispatchRefused):
                ProviderResponseAuthority(budget).forward(source, delivered.append)
        else:
            budget.dispatch(call(1), b"prior", lambda _: None)
        if route == "mcp":
            source, request = mcp_frames()
            source["params"].update(threadId="thread", turnId="turn")
            request["params"]["_meta"].update(
                threadId="thread",
                **{"x-codex-turn-metadata": {"thread_id": "thread", "turn_id": "turn"}},
            )
            records["00000004-received.json"] = source
            mcp_root = tmp_path / "mcp"
            with MCPHarnessAuthority(
                mcp_root,
                timeout_s=1,
                handle_call=lambda call, raw: budget.dispatch(
                    call, raw, lambda _: pytest.fail("delivered")
                ),
            ) as authority:
                authority.observe(encode(source), thread_id="thread", turn_id="turn")
                with pytest.raises(DispatchRefused):
                    authority.dispatch(encode(request))
            artifacts["mcp-harness-reference.json"] = encode(authority.reference())
            artifacts.update({"mcp-harness/" + p.name: p.read_bytes() for p in mcp_root.iterdir()})
            mcp_count = 1
            if enabled:
                launch["mcp_harness"] = True
        else:
            binding = {"base_url": "http://127.0.0.1:1/v1"}
            provider = {
                "00000001-request.body": REQUEST if route == "hosted" else b"{}",
                "00000001-request.json": encode(
                    {"content_encoding": "identity", "authorization_present": False}
                ),
            }
            if route == "hosted":
                binding["hosted_tool_contract"] = "aisle.fixture.responses.max_tool_calls.v1"
                with pytest.raises(DispatchRefused):
                    budget.hosted_response("00000001", REQUEST, lambda _: pytest.fail("upstream"))
                delivery = {"sequence": None, "events_returned": 0, "error_type": "DispatchRefused"}
            else:
                provider["00000001-response.sse"] = source
                delivery = {
                    "sequence": 1,
                    "events_returned": len(delivered),
                    "error_type": "DispatchRefused",
                }
            provider["00000001-delivery.json"] = encode(delivery)
            provider["invocation.json"] = encode(
                {
                    "schema_version": "aisle.provider-relay.v1",
                    "session_id": "session",
                    "binding": binding,
                    "delegated_tools": [],
                    "complete_coverage": False,
                    "confinement_verified": False,
                }
            )
            artifacts["provider-reference.json"] = encode(
                {
                    "artifacts": {n: hashlib.sha256(v).hexdigest() for n, v in provider.items()},
                    "bytes": sum(map(len, provider.values())),
                    "failure": "DispatchRefused",
                    "complete_coverage": False,
                    "confinement_verified": False,
                }
            )
            artifacts.update({"provider/" + n: v for n, v in provider.items()})
            if enabled:
                launch["provider"] = binding
    protocol = {n: encode(v) for n, v in records.items()}
    artifacts.update({"frontend-protocol/" + n: v for n, v in protocol.items()})
    artifacts["frontend-protocol-reference.json"] = encode(
        {
            "thread_id": "thread",
            "turn_id": "turn",
            "dynamic_calls": 0,
            "mcp_calls": mcp_count,
            "stream_complete": False,
            "failure": records["failure.json"],
            "artifacts": {n: hashlib.sha256(v).hexdigest() for n, v in protocol.items()},
        }
    )
    artifacts["frontend-authority-reference.json"] = encode(
        {"session_id": "session", "artifacts": {}}
    )
    artifacts["frontend-dispatch-reference.json"] = encode(budget.reference())
    artifacts.update({"frontend-dispatch/" + p.name: p.read_bytes() for p in root.iterdir()})
    proof = {
        "artifacts": artifacts,
        "record": {"session_id": "session", "plan_id": "plan", "arm": "typed"},
        "admission": {
            "immutable_id": "plan",
            "arms": {
                "typed": {
                    "budget": {"frontend_tool_ceiling": 1},
                    "policy": {"allowed_external_tools": operations},
                }
            },
            "launch_bindings": {"typed": launch},
        },
    }
    if enabled:
        result = refusal_source_evidence(proof)
        assert result["linked_hosted_requests"] == (["00000001"] if route == "hosted" else [])
        assert result["linked_local_attempts"] == ([] if route == "hosted" else [2])
        assert result["complete_coverage"] is False
    else:
        with pytest.raises(ValueError):
            refusal_source_evidence(proof)
