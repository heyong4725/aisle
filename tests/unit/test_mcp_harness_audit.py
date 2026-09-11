"""MON-12/MON-13: MCP replay checks semantic links beyond matching file hashes."""

import copy
import hashlib
import json

import pytest
from test_mcp_harness_source import frames

pytestmark = pytest.mark.unit


def evidence(tmp_path):
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority

    source, request = frames()
    source_bytes = json.dumps(source).encode()
    output = tmp_path / "authority"
    with MCPHarnessAuthority(
        output,
        timeout_s=5,
        handle_call=lambda *args: {
            "success": True,
            "contentItems": [{"type": "inputText", "text": "checked"}],
        },
    ) as authority:
        authority.observe(source_bytes, thread_id="child", turn_id="child-turn")
        authority.dispatch(json.dumps(request).encode())
    key = ("child-turn", "call-check")
    completed = {
        **source["params"]["item"],
        "status": "completed",
        "error": None,
        "result": {"content": [{"type": "text", "text": "checked"}]},
    }
    return {
        "artifacts": {path.name: path.read_bytes() for path in output.iterdir()},
        "expected": authority.reference(),
        "byte_limit": 65536,
        "sources": {key: source_bytes},
        "completions": {key: completed},
    }


def test_mcp_replay_matches_owned_source_and_returned_result(tmp_path):
    """MON-12: raw source, MCP request and frontend completion agree for one callback."""
    from aisle.harness.mcp_harness_source import verify_mcp_sources

    verify_mcp_sources(**evidence(tmp_path))


@pytest.mark.parametrize(
    "fault",
    [
        "request_call",
        "reply",
        "completion_tool",
        "missing_delivery",
        "extra_artifact",
        "missing_completion",
    ],
)
def test_mcp_replay_refuses_semantic_drift_with_recomputed_hashes(tmp_path, fault):
    """MON-13: rehashing changed files cannot repair a broken owned-source chain."""
    from aisle.harness.mcp_harness_source import verify_mcp_sources

    retained = copy.deepcopy(evidence(tmp_path))
    artifacts = retained["artifacts"]
    if fault == "request_call":
        value = json.loads(artifacts["00000001-request.frame"])
        value["params"]["_meta"]["callId"] = "different"
        artifacts["00000001-request.frame"] = json.dumps(value).encode()
    elif fault == "reply":
        value = json.loads(artifacts["00000001-result.json"])
        value["contentItems"][0]["text"] = "invented result"
        artifacts["00000001-result.json"] = json.dumps(value).encode()
    elif fault == "completion_tool":
        retained["completions"][("child-turn", "call-check")]["tool"] = "run"
    elif fault == "missing_delivery":
        del artifacts["00000001-delivery.json"]
    elif fault == "extra_artifact":
        artifacts["unmatched.frame"] = b"{}"
    else:
        retained["completions"].clear()
    retained["expected"]["artifacts"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()
    }
    with pytest.raises(ValueError):
        verify_mcp_sources(**retained)
