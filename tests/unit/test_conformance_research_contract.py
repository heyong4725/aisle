"""MON-8/MON-13: all unified route cases share one explicit hosted fixture intent."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


def test_unified_contract_is_identical_across_arms_and_routes():
    """MON-8: selecting a probe route cannot silently change the admitted prompt."""
    from test_matched_conformance_session import _research_contract

    from aisle.harness.frontend_effects import hosted_probe_set

    targets = {"typed": "task.yaml", "monolithic": "agent.py"}
    candidates = {
        arm: {"repository": {"editable_allowlist": [target]}} for arm, target in targets.items()
    }
    contracts = {
        _research_contract(
            candidates, route=route, unified=True, target=target, marker="# AISLE probe contract"
        )
        for route in (
            "harness",
            "harness_child",
            "native",
            "native_edit",
            "continued_input",
            "nested",
            "mcp",
            "hosted",
            "subagents",
        )
        for target in targets.values()
    }
    assert len(contracts) == 1
    contract = contracts.pop()
    assert hosted_probe_set(targets, "# AISLE probe contract") in contract.splitlines()


@pytest.mark.parametrize("route", ["hosted", "native"])
def test_legacy_contract_preserves_its_existing_probe_scope(route):
    """MON-13: historical single-route captures retain their explicit single intent."""
    from test_matched_conformance_session import _research_contract

    from aisle.harness.frontend_effects import hosted_probe

    actual = _research_contract(
        {}, route=route, unified=False, target="task.yaml", marker="# AISLE probe contract"
    )
    expected = "research contract"
    if route == "hosted":
        expected += "\n" + hosted_probe("task.yaml", "# AISLE probe contract")
    assert actual == expected
