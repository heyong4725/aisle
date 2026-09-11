"""MON-12/MON-13: an enabled but unused nested host has a valid empty source closure."""

import pytest

from aisle.harness.code_mode_audit import verify_code_mode_sources
from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("route", [None, "native", "nested"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_empty_rpc_closure_requires_no_nested_reservations(tmp_path, route, interrupted):
    """MON-13: empty nested evidence may coexist with native work, never hide nested charges."""
    root = tmp_path / "dispatch"
    with DispatchBudget(root, session_id="session", ceiling=2) as budget:
        if route is not None:

            def deliver(_):
                if interrupted:
                    raise RuntimeError("injected forwarding interruption")

            try:
                budget.dispatch(
                    {
                        "turn_id": "code-mode:execution"
                        if route == "nested"
                        else "provider-response",
                        "call_id": "call",
                        "tool_name": "exec_command",
                    },
                    b"source",
                    deliver,
                )
            except RuntimeError:
                assert interrupted
    report = verify_code_mode_sources(
        {},
        expected={
            "artifacts": {},
            "bytes": 0,
            "failure": None,
            "complete_coverage": False,
            "confinement_verified": False,
        },
        delegated_tools=set(),
        byte_limit=65536,
        dispatch={
            "artifacts": {p.name: p.read_bytes() for p in root.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 65536,
        },
    )
    assert report["ok"] is (route != "nested"), report
    assert not report["complete_coverage"] and not report["confinement_verified"]
    if report["ok"]:
        assert report["native_attempts"] == report["delegated_calls"] == []
