"""MON-13: prepared fault cases keep their controller and real injection wrapper."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault,wrapper,patched,parameter",
    [
        (
            "controller_unavailable",
            "controller_unavailable_retains_actual_frontend_request",
            True,
            False,
        ),
        ("controller_timeout", "controller_timeout_retains_actual_frontend_request", True, False),
        ("malformed_response", "malformed_response_retains_rejected_bytes", True, False),
        ("denial", "tool_denial_reaches_actual_frontend", False, False),
        ("hook_absent", "budget_refusal_is_independent_of_frontend_hook", False, True),
        ("hook_changed", "budget_refusal_is_independent_of_frontend_hook", False, True),
        ("provider_replay", "provider_replay_retains_unexecuted_effect", False, False),
        ("concurrent_nested", "concurrent_nested_calls_share_last_slot", False, False),
        ("cancel_before_forward", "interrupted_delivery_keeps_reservation", True, True),
        ("cancel_after_forward", "interrupted_delivery_keeps_reservation", True, True),
    ],
)
def test_prepared_fault_selects_injection_after_controller_activation(
    tmp_path, monkeypatch, fault, wrapper, patched, parameter
):
    """MON-13: entry selection is tested without claiming actual fault qualification."""
    import conformance_run_fixture as preparation
    import test_matched_conformance_session as fixture

    for name, value in {
        "ROOT": str(tmp_path),
        "ARM": "typed",
        "FAULT": fault,
        "REFERENCE": "/reference",
        "OUTPUT": "session-fault",
    }.items():
        monkeypatch.setenv("AISLE_PREPARED_RUN_" + name, value)
    calls = []
    monkeypatch.setattr(
        preparation, "activate_prepared_controller", lambda path: calls.append(path)
    )

    def selected(**kwargs):
        assert calls == [tmp_path / "controller"]
        calls.append(kwargs)

    monkeypatch.setattr(fixture, "test_admitted_" + wrapper, selected)
    fixture.test_prepared_fault_through_actual_frontend()
    assert len(calls) == 2
    args = calls[1]
    assert args.pop("tmp_path") == tmp_path and args.pop("arm") == "typed"
    assert args.pop("unified") is True
    assert args.pop("run_setup") == {
        "base": tmp_path,
        "operation": "check",
        "reference_session": "/reference",
        "output_name": "session-fault",
    }
    if patched:
        assert isinstance(args.pop("monkeypatch"), pytest.MonkeyPatch)
    if parameter:
        assert args.pop("fault") == fault
    assert not args
