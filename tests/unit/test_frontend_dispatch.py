"""MON-8/MON-12/MON-13: reservations precede delivery of covered frontend calls."""

import concurrent.futures
import json
import threading

import pytest

pytestmark = pytest.mark.unit


def call(index):
    return {"turn_id": "turn", "call_id": str(index), "tool_name": "exec_command"}


def test_durable_reservation_precedes_delivery_and_ceiling_prevents_side_effect(tmp_path):
    """MON-8/MON-12: an excess call is refused before its delivery callback can execute."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    output = tmp_path / "dispatch"
    effects = []
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:

        def deliver(frame):
            row = json.loads((output / "00000001-reservation.json").read_text())
            assert row["decision"] == "authorized"
            assert row["reserved_after"] == 1
            assert (output / "00000001.frame").read_bytes() == frame
            effects.append(frame)

        budget.dispatch(call(1), b"first", deliver)
        with pytest.raises(DispatchRefused, match="budget"):
            budget.dispatch(call(2), b"second", effects.append)
    assert effects == [b"first"]
    row = json.loads((output / "00000002-reservation.json").read_text())
    assert row["decision"] == "refused"
    assert row["reserved_after"] == 1


def test_concurrent_calls_share_one_reservation_authority(tmp_path):
    """MON-8: simultaneous requests cannot both consume the final available slot."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    barrier = threading.Barrier(8)
    effects = []
    with DispatchBudget(tmp_path / "dispatch", session_id="session", ceiling=1) as budget:

        def invoke(index):
            barrier.wait(timeout=5)
            try:
                budget.dispatch(call(index), str(index).encode(), effects.append)
                return True
            except DispatchRefused:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(invoke, range(8)))
    assert sum(results) == len(effects) == 1
    records = [
        json.loads(path.read_text()) for path in (tmp_path / "dispatch").glob("*-reservation.json")
    ]
    assert len(records) == 8
    assert sum(row["decision"] == "authorized" for row in records) == 1


def test_replayed_identity_cannot_deliver_again_even_with_changed_payload(tmp_path):
    """MON-13: peer replay cannot acquire a second delivery using an existing call identity."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    effects = []
    with DispatchBudget(tmp_path / "dispatch", session_id="session", ceiling=3) as budget:
        budget.dispatch(call(1), b"first", effects.append)
        with pytest.raises(DispatchRefused, match="replay"):
            budget.dispatch(call(1), b"changed", effects.append)
    assert effects == [b"first"]


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_uncertain_delivery_keeps_charge_and_closes_authority(tmp_path, failure):
    """MON-12/MON-13: partial delivery or cancellation must not refund an uncertain call."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    effects = []
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=3) as budget:

        def deliver(frame):
            effects.append(frame)
            raise failure("after partial delivery")

        with pytest.raises(failure):
            budget.dispatch(call(1), b"first", deliver)
        with pytest.raises(DispatchRefused, match="closed"):
            budget.dispatch(call(2), b"second", effects.append)
    assert effects == [b"first"]
    reservation = json.loads((output / "00000001-reservation.json").read_text())
    terminal = json.loads((output / "00000001-delivery.json").read_text())
    assert reservation["reserved_after"] == 1
    assert terminal["status"] == "uncertain"
    assert terminal["error_type"] == failure.__name__


def test_failed_retention_prevents_delivery_and_further_authorization(tmp_path, monkeypatch):
    """MON-13: failed durable retention cannot release a call or leave a reusable budget."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    effects = []
    with DispatchBudget(tmp_path / "dispatch", session_id="session", ceiling=3) as budget:

        def fail(*args):
            raise OSError("retention unavailable")

        monkeypatch.setattr(budget, "_retain", fail)
        with pytest.raises(OSError):
            budget.dispatch(call(1), b"first", effects.append)
        with pytest.raises(DispatchRefused, match="closed"):
            budget.dispatch(call(2), b"second", effects.append)
    assert effects == []


def test_existing_authority_directory_cannot_reset_budget(tmp_path):
    """MON-13: another controller cannot silently resume an existing session at zero usage."""
    from aisle.harness.frontend_dispatch import DispatchBudget

    path = tmp_path / "dispatch"
    with DispatchBudget(path, session_id="session", ceiling=1):
        with pytest.raises(FileExistsError):
            DispatchBudget(path, session_id="session", ceiling=1)
    with pytest.raises(FileExistsError):
        DispatchBudget(path, session_id="session", ceiling=1)


@pytest.mark.parametrize("stage", ["frame", "reservation", "delivery"])
def test_retention_failure_after_write_never_reopens_authority(tmp_path, monkeypatch, stage):
    """MON-13: even a write that reached disk cannot authorize further work after uncertainty."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    effects = []
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=2) as budget:
        retain = budget._retain
        target = {
            "frame": "00000001.frame",
            "reservation": "00000001-reservation.json",
            "delivery": "00000001-delivery.json",
        }[stage]

        def fail_after_write(name, data):
            retain(name, data)
            if name == target:
                raise OSError("injected failure after writing receipt")

        monkeypatch.setattr(budget, "_retain", fail_after_write)
        with pytest.raises(OSError):
            budget.dispatch(call(1), b"first", effects.append)
        with pytest.raises(DispatchRefused, match="closed"):
            budget.dispatch(call(2), b"second", effects.append)
    assert effects == ([b"first"] if stage == "delivery" else [])
    if stage == "delivery":
        error = json.loads((output / "00000001-delivery-error.json").read_text())
        assert error["status"] == "uncertain"
