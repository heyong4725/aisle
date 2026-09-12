"""MON-13: prepared route cases retain the selected controller and operation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "route,operation",
    [
        ("harness", "check"),
        ("harness", "run"),
        ("native", "check"),
        ("native_edit", "check"),
        ("continued_input", "check"),
        ("nested", "check"),
        ("mcp", "check"),
        ("hosted", "check"),
        ("subagents", "check"),
        ("harness_child", "check"),
    ],
)
@pytest.mark.parametrize("refused", [False, True])
def test_prepared_entry_selects_route_without_replacing_controller(
    tmp_path, monkeypatch, route, operation, refused
):
    """MON-13: every case targets the prepared base; refusal never falls back to another fixture."""
    import test_matched_conformance_session as fixture

    for name, value in {
        "ROOT": str(tmp_path),
        "ARM": "typed",
        "ROUTE": route,
        "OPERATION": operation,
        "REFUSED": "1" if refused else "0",
        "OUTPUT": "session-selected",
        "REFERENCE": "/reference",
    }.items():
        monkeypatch.setenv("AISLE_PREPARED_RUN_" + name, value)
    calls = []
    monkeypatch.setattr(
        fixture,
        "test_admitted_actual_session_composes_source_execution_and_effect",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    fixture.test_prepared_harness_run_through_actual_frontend()
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (tmp_path, "typed", refused, route)
    assert kwargs["unified"] is True
    assert kwargs["run_setup"] == {
        "base": tmp_path,
        "operation": operation,
        "reference_session": "/reference",
        "output_name": "session-selected",
    }
