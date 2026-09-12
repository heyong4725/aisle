"""MON-8/MON-13: excess calls are offered before another hosted allowance is needed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


def test_child_start_reserves_spawn_and_wait_before_child_effect(tmp_path):
    """MON-8/MON-13: parent coordination leaves the third slot to the owned child."""
    import json

    from test_matched_conformance_session import _child_start_frame

    from aisle.harness.frontend_dispatch import DispatchBudget
    from aisle.harness.provider_response_authority import ProviderResponseAuthority, _response

    source = _child_start_frame(1, wall_ceiling_s=30)
    _, _, calls = _response(source)
    assert [call[0] for call in calls.values()] == ["call_1", "call_wait_1"]
    response = next(
        json.loads(line[6:])["response"]
        for line in source.splitlines()
        if line.startswith(b"data: ") and json.loads(line[6:])["type"] == "response.completed"
    )
    items = response["output"]
    assert [item["name"] for item in items] == ["spawn_agent", "wait_agent"]
    assert all(item["namespace"] == "collaboration" for item in items)
    assert json.loads(items[1]["arguments"]) == {"timeout_ms": 30000}
    with DispatchBudget(tmp_path / "dispatch", session_id="child-start", ceiling=4) as budget:
        ProviderResponseAuthority(budget).forward(source, lambda _: None)
    assert budget.reference()["attempts"] == budget.reference()["reserved"] == 2


@pytest.mark.parametrize("route", ["native", "native_edit", "continued_input"])
def test_batched_native_effect_is_refused_before_forwarding(tmp_path, route):
    """MON-8: the last slot authorizes the prefix but never releases the excess effect."""
    from test_frontend_continued_input import _frame
    from test_matched_conformance_session import _batch_frames, _effect_frame

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
    from aisle.harness.provider_response_authority import ProviderResponseAuthority, _response

    effect = _effect_frame(
        5,
        route,
        target="task.yaml",
        marker="# AISLE probe batch",
        before=b"name: fixture\n",
        operation="check",
        body={
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_2",
                    "output": "Process running with session ID 123",
                }
            ]
        },
    )
    source = _batch_frames(_frame(4, "exec_command", {"cmd": ":", "login": False}), effect)
    _, _, calls = _response(source)
    assert [call[0] for call in calls.values()] == ["call_4", "call_5"]
    forwarded = []
    with DispatchBudget(tmp_path / "dispatch", session_id="batch", ceiling=4) as budget:
        for index in range(3):
            budget.dispatch(
                {"turn_id": "earlier", "call_id": str(index), "tool_name": "exec_command"},
                b"earlier",
                lambda _: None,
            )
        authority = ProviderResponseAuthority(budget)
        with pytest.raises(DispatchRefused, match="exhausted"):
            authority.forward(source, forwarded.append)
    assert b"call_4" in b"".join(forwarded)
    assert b"call_5" not in b"".join(forwarded)
    assert budget.reference()["attempts"] == 5
    assert budget.reference()["reserved"] == 4
