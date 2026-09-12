"""MON-8/MON-13: nested observation covers the admitted tool deadline."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("seconds,milliseconds", [(30, 30000), (2370, 2370000), (0.0001, 1)])
def test_nested_observation_preserves_script(seconds, milliseconds):
    """MON-13: observation uses the existing budget without changing the probe."""
    from aisle.harness.frontend_effects import nested_execution

    script = 'await tools.harness__check({});\ntext("finished");'
    header, body = nested_execution(script, wall_ceiling_s=seconds).split("\n", 1)
    assert header.startswith("// @exec: ")
    assert json.loads(header.removeprefix("// @exec: ")) == {"yield_time_ms": milliseconds}
    assert body == script


@pytest.mark.parametrize(
    "seconds", [True, False, 0, -1, "30", None, float("nan"), float("inf"), 2**64]
)
def test_nested_observation_rejects_invalid_budget(seconds):
    """MON-8: a malformed deadline cannot become a different execution allowance."""
    from aisle.harness.frontend_effects import nested_execution

    with pytest.raises(ValueError, match="observation"):
        nested_execution("text(1);", wall_ceiling_s=seconds)
