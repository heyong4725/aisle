"""MON-12/MON-13: MCP metadata must match the owned frontend source event."""

import copy
import json

import pytest

pytestmark = pytest.mark.unit


def frames():
    source = {
        "method": "item/started",
        "params": {
            "threadId": "child",
            "turnId": "child-turn",
            "item": {
                "type": "mcpToolCall",
                "id": "call-check",
                "server": "aisle_harness",
                "tool": "check",
                "status": "inProgress",
                "arguments": {},
            },
        },
    }
    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "check",
            "arguments": {},
            "_meta": {
                "callId": "call-check",
                "threadId": "child",
                "x-codex-turn-metadata": {"thread_id": "child", "turn_id": "child-turn"},
            },
        },
    }
    return source, request


def test_mcp_request_binds_the_owned_child_call():
    """MON-12: the request's call, thread, turn, tool and arguments match one source."""
    from aisle.harness.mcp_harness_source import match_mcp_request

    source, request = frames()
    assert match_mcp_request(
        json.dumps(source).encode(),
        json.dumps(request).encode(),
        thread_id="child",
        turn_id="child-turn",
    ) == {"turn_id": "child-turn", "call_id": "call-check", "tool_name": "harness.check"}


@pytest.mark.parametrize(
    "fault",
    ["no_source", "call", "thread", "turn", "operation", "arguments", "server", "duplicate"],
)
def test_mcp_metadata_cannot_replace_owned_source(fault):
    """MON-13: missing or changed source binding refuses authorization before dispatch."""
    from aisle.harness.mcp_harness_source import match_mcp_request

    source, request = copy.deepcopy(frames())
    if fault == "call":
        request["params"]["_meta"]["callId"] = "other"
    elif fault == "thread":
        request["params"]["_meta"]["threadId"] = "other"
    elif fault == "turn":
        request["params"]["_meta"]["x-codex-turn-metadata"]["turn_id"] = "other"
    elif fault == "operation":
        request["params"]["name"] = "run"
    elif fault == "arguments":
        request["params"]["arguments"] = {"injected": True}
    elif fault == "server":
        source["params"]["item"]["server"] = "other"
    raw_source = b"" if fault == "no_source" else json.dumps(source).encode()
    raw_request = json.dumps(request).encode()
    if fault == "duplicate":
        raw_request = raw_request.replace(b'"name": "check"', b'"name": "run", "name": "check"')
    with pytest.raises(ValueError):
        match_mcp_request(raw_source, raw_request, thread_id="child", turn_id="child-turn")
