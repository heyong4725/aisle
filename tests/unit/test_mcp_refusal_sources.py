"""MON-12/MON-13: MCP refusal requires the owned source and exhausted shared reservation."""

import hashlib
import json

import pytest
from test_frontend_dispatch import call
from test_mcp_harness_source import frames

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.mcp_harness_authority import MCPHarnessAuthority

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "source",
        "request",
        "error",
        "result",
        "completion",
        "refusal_completion",
        "unrelated_failure",
        "wrong_args",
    ],
)
def test_mcp_refusal_binds_source_and_reservation(tmp_path, drift):
    """MON-13: unrelated MCP failures and caller metadata cannot substitute for quota evidence."""
    from aisle.harness.mcp_harness_source import link_mcp_refusal_sources

    source, request = [json.dumps(row).encode() for row in frames()]
    output = tmp_path / "mcp"
    dispatch_root = tmp_path / "dispatch"
    with DispatchBudget(dispatch_root, session_id="session", ceiling=1) as budget:
        budget.dispatch(call(1), b"earlier", lambda _: None)
        with MCPHarnessAuthority(
            output,
            timeout_s=1,
            handle_call=lambda call, raw: budget.dispatch(
                call, raw, lambda _: pytest.fail("delivered")
            ),
        ) as authority:
            authority.observe(source, thread_id="child", turn_id="child-turn")
            with pytest.raises(DispatchRefused):
                authority.dispatch(request)
    artifacts = {p.name: p.read_bytes() for p in output.iterdir()}
    reference = authority.reference()
    original = json.loads(source)["params"]["item"]
    key = ("child-turn", original["id"])
    sources, completions = {key: source}, {}
    if drift == "source":
        sources[key] = b"{}"
    elif drift == "request":
        row = json.loads(request)
        row["params"]["name"] = "run"
        artifacts["00000001-request.frame"] = json.dumps(row).encode()
    elif drift == "error":
        artifacts["00000001-delivery.json"] = (
            b'{"handler_entered":true,"error_type":"TimeoutError"}'
        )
    elif drift == "result":
        artifacts["00000001-result.json"] = b'{"success":true,"contentItems":[]}'
    elif drift == "completion":
        completions[key] = {**original, "status": "completed"}
    if drift in {"refusal_completion", "unrelated_failure", "wrong_args"}:
        completions[key] = {
            **original,
            "status": "failed",
            "result": None,
            "error": {
                "message": (
                    "tool call error: tool call failed for `aisle_"
                    "harness/check`\n\nCaused by:\n    Mcp error: -32"
                    "000: MCP harness authorization unavailable"
                )
            },
        }
        if drift == "unrelated_failure":
            completions[key]["error"]["message"] = "connection timed out"
        elif drift == "wrong_args":
            completions[key]["arguments"] = {"other": True}
    reference["artifacts"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()
    }
    arguments = dict(
        expected=reference,
        byte_limit=100000,
        sources=sources,
        completions=completions,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in dispatch_root.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 100000,
        },
    )
    if drift in {None, "refusal_completion"}:
        assert link_mcp_refusal_sources(artifacts, **arguments) == [2]
    else:
        with pytest.raises(ValueError):
            link_mcp_refusal_sources(artifacts, **arguments)
