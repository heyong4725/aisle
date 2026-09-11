"""MON-13: fault replay cannot relax ordinary dispatch completion checks."""

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget
from aisle.harness.provider_source_audit import _Reservations

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("selected", [None, 1, 2])
def test_uncertain_delivery_requires_explicit_exact_attempt(tmp_path, selected):
    """MON-13: only the separately audited failed delivery may remain uncertain."""
    output = tmp_path / "dispatch"

    def unavailable(_):
        raise ValueError("controller unavailable")

    with DispatchBudget(output, session_id="session", ceiling=2) as budget:
        with pytest.raises(ValueError):
            budget.dispatch(
                {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"},
                b"source",
                unavailable,
            )
    dispatch = {
        "artifacts": {p.name: p.read_bytes() for p in output.iterdir()},
        "expected": budget.reference(),
        "byte_limit": 65536,
    }
    kwargs = {} if selected is None else {"uncertain_attempts": frozenset({selected})}
    if selected == 1:
        replay = _Reservations(dispatch, **kwargs)
        assert not replay.used
    else:
        with pytest.raises(ValueError, match="invalid provider dispatch journal"):
            _Reservations(dispatch, **kwargs)
