"""MON-12/MON-13: native refusal must not hide prior controller-MCP source drift."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case
from test_mcp_harness_source import frames as mcp_frames
from test_provider_response_authority import frames, item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.frontend_qualification import refusal_source_evidence
from aisle.harness.mcp_harness_authority import MCPHarnessAuthority
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


def encode(value):
    return json.dumps(value).encode() + b"\n"


@pytest.mark.parametrize(
    "drift", [None, "mcp_result", "mcp_request", "missing_completion", "provider_args"]
)
def test_native_refusal_replays_prior_mcp_exchange(tmp_path, drift):
    """MON-13: replay the successful MCP request/result and provider delegation before native
    refusal.
    """
    source, request = mcp_frames()
    source["params"].update(threadId="thread", turnId="turn")
    request["params"]["_meta"].update(
        threadId="thread", **{"x-codex-turn-metadata": {"thread_id": "thread", "turn_id": "turn"}}
    )
    source_raw, request_raw = encode(source), encode(request)
    reply = {"success": True, "contentItems": [{"type": "inputText", "text": "controller result"}]}
    delegated = {
        ("harness", "check", "function_call"),
        ("mcp__aisle_harness", "check", "function_call"),
    }
    delegate = {
        "type": "function_call",
        "id": "mcp-item",
        "call_id": source["params"]["item"]["id"],
        "namespace": "mcp__aisle_harness",
        "name": "check",
        "arguments": "{}",
    }
    first, second = frames([delegate], response_id="mcp-response"), frames([item(1), item(2)])
    delivered_first, delivered_second = [], []
    with DispatchBudget(tmp_path / "dispatch", session_id="session", ceiling=2) as budget:
        provider_authority = ProviderResponseAuthority(budget, delegated_tools=delegated)
        provider_authority.forward(first, delivered_first.append)

        def handle(call, raw):
            budget.dispatch(call, raw, lambda _: None)
            return reply

        with MCPHarnessAuthority(tmp_path / "mcp", timeout_s=1, handle_call=handle) as mcp:
            mcp.observe(source_raw, thread_id="thread", turn_id="turn")
            mcp.dispatch(request_raw)
        with pytest.raises(DispatchRefused):
            provider_authority.forward(second, delivered_second.append)
    records, _ = _case()
    del records["00000005-sent.json"]
    records["00000004-received.json"] = source
    records["00000005-received.json"] = {
        "method": "item/completed",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "item": {
                **source["params"]["item"],
                "status": "completed",
                "result": {"content": [{"type": "text", "text": "controller result"}]},
                "error": None,
            },
        },
    }
    records["failure.json"] = {"error_type": "ValueError", "error": "provider failed"}
    if drift == "missing_completion":
        del records["00000005-received.json"]
    protocol = {name: encode(value) for name, value in records.items()}
    protocol_ref = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 0,
        "mcp_calls": 1,
        "stream_complete": False,
        "failure": records["failure.json"],
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in protocol.items()},
    }
    mcp_files = {p.name: p.read_bytes() for p in (tmp_path / "mcp").iterdir()}
    if drift == "mcp_result":
        mcp_files["00000001-result.json"] = encode(
            {"success": True, "contentItems": [{"type": "inputText", "text": "changed"}]}
        )
    elif drift == "mcp_request":
        request["params"]["name"] = "run"
        mcp_files["00000001-request.frame"] = encode(request)
    elif drift == "provider_args":
        delegate["arguments"] = '{"changed":true}'
        first = frames([delegate], response_id="mcp-response")
    mcp_ref = mcp.reference()
    mcp_ref["artifacts"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in mcp_files.items()
    }
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
    for number, raw, delivered in ((1, first, delivered_first), (2, second, delivered_second)):
        provider.update(
            {
                f"{number:08d}-request.body": b"{}",
                f"{number:08d}-request.json": encode({"content_encoding": "identity"}),
                f"{number:08d}-response.sse": raw,
                f"{number:08d}-delivery.json": encode(
                    {
                        "sequence": number,
                        "events_returned": len(delivered),
                        "error_type": None if number == 1 else "DispatchRefused",
                    }
                ),
            }
        )
    provider_ref = {
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in provider.items()},
        "bytes": sum(map(len, provider.values())),
        "failure": "DispatchRefused",
        "complete_coverage": False,
        "confinement_verified": False,
    }
    artifacts = {"frontend-protocol/" + name: raw for name, raw in protocol.items()}
    artifacts.update(
        {"frontend-dispatch/" + p.name: p.read_bytes() for p in (tmp_path / "dispatch").iterdir()}
    )
    artifacts.update({"provider/" + name: raw for name, raw in provider.items()})
    artifacts.update({"mcp-harness/" + name: raw for name, raw in mcp_files.items()})
    artifacts.update(
        {
            "frontend-protocol-reference.json": encode(protocol_ref),
            "frontend-dispatch-reference.json": encode(budget.reference()),
            "provider-reference.json": encode(provider_ref),
            "mcp-harness-reference.json": encode(mcp_ref),
            "frontend-authority-reference.json": encode({"session_id": "session", "artifacts": {}}),
        }
    )
    proof = {
        "artifacts": artifacts,
        "record": {"arm": "typed", "session_id": "session", "plan_id": "plan"},
        "admission": {
            "immutable_id": "plan",
            "arms": {
                "typed": {
                    "budget": {"frontend_tool_ceiling": 2},
                    "policy": {"allowed_external_tools": ["harness.check"]},
                }
            },
            "launch_bindings": {
                "typed": {"app_server": {}, "provider": binding, "mcp_harness": True}
            },
        },
    }
    if drift is None:
        report = refusal_source_evidence(proof)
        assert report["source_audits"]["mcp_prefix"]["completed_calls"] == 1
    else:
        with pytest.raises(ValueError):
            refusal_source_evidence(proof)
