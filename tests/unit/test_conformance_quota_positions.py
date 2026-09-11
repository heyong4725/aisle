"""MON-8/MON-13: conformance targets must cross the actual dispatch quota."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "route",
    [
        "harness",
        "native",
        "native_edit",
        "mcp",
        "nested",
        "continued_input",
        "subagents",
        "harness_child",
    ],
)
@pytest.mark.parametrize("unified", [False, True])
@pytest.mark.parametrize("refused", [False, True])
def test_selected_effect_crosses_real_dispatch_boundary(tmp_path, route, unified, refused):
    """MON-8/MON-13: padding consumes the declared quota before the selected refused effect."""
    from test_matched_conformance_session import _case_positions

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    ceiling, effect = _case_positions(route, unified=unified, refused=refused)
    budget = DispatchBudget(tmp_path / "dispatch", session_id="fixture", ceiling=ceiling)
    delivered = []
    try:
        for number in range(1, effect):
            budget.dispatch(
                {"turn_id": "turn", "call_id": str(number), "tool_name": "exec_command"},
                b"padding",
                lambda *args: None,
            )
        call = {"turn_id": "turn", "call_id": str(effect), "tool_name": route}
        if refused:
            with pytest.raises(DispatchRefused):
                budget.dispatch(call, b"effect", lambda *args: delivered.append(True))
            assert not delivered
        else:
            budget.dispatch(call, b"effect", lambda *args: delivered.append(True))
            assert delivered == [True]
    finally:
        budget.close()
