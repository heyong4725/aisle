"""MON-12/MON-13: replay retained dispatch decisions against a trusted reference."""

import hashlib
import json

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit


def snapshot(path, *, attempts, reserved, ceiling):
    artifacts = {p.name: p.read_bytes() for p in path.iterdir()}
    expected = {
        "session_id": "session",
        "ceiling": ceiling,
        "attempts": attempts,
        "reserved": reserved,
        "artifacts": {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()},
    }
    return artifacts, expected


def journal(tmp_path, ceiling=1):
    path = tmp_path / "dispatch"
    with DispatchBudget(path, session_id="session", ceiling=ceiling) as budget:
        for index in (1, 2):
            try:
                budget.dispatch(
                    {"turn_id": "turn", "call_id": str(index), "tool_name": "exec_command"},
                    str(index).encode(),
                    lambda frame: None,
                )
            except DispatchRefused:
                pass
    return snapshot(path, attempts=2, reserved=min(2, ceiling), ceiling=ceiling)


def change(artifacts, expected, name, update):
    row = json.loads(artifacts[name])
    row.update(update)
    artifacts[name] = json.dumps(row).encode()
    # Rebind the fixture reference to model an internally inconsistent producer,
    # independently of the tests for tampering against an unchanged reference.
    expected["artifacts"][name] = hashlib.sha256(artifacts[name]).hexdigest()


def test_replay_authorization_and_refusal_without_claiming_full_coverage(tmp_path):
    """MON-12: intact refusal evidence replays without promoting partial coverage."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    artifacts, expected = journal(tmp_path)
    result = verify_dispatch_journal(artifacts, expected=expected, byte_limit=65536)
    assert result["ok"], result
    assert result["attempts"] == 2 and result["reserved"] == 1
    assert result["delivery_uncertain"] is False
    assert result["complete_coverage"] is False
    assert result["confinement_verified"] is False


@pytest.mark.parametrize("damage", ["frame", "missing_tail", "extra"])
def test_reference_detects_changed_missing_and_unmatched_artifacts(tmp_path, damage):
    """MON-13: a retained reference detects alterations and missing final refusals."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    artifacts, expected = journal(tmp_path)
    if damage == "frame":
        artifacts["00000001.frame"] = b"changed"
    elif damage == "missing_tail":
        del artifacts["00000002-reservation.json"]
    else:
        artifacts["extra.json"] = b"{}"
    assert not verify_dispatch_journal(artifacts, expected=expected, byte_limit=65536)["ok"]


@pytest.mark.parametrize("damage", ["counter", "replay", "uncertain_then_call", "session"])
def test_bound_but_inconsistent_producer_records_are_rejected(tmp_path, damage):
    """MON-13: matching artifact hashes do not legitimize an impossible dispatch history."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    artifacts, expected = journal(tmp_path, ceiling=2)
    if damage == "counter":
        change(artifacts, expected, "00000002-reservation.json", {"reserved_after": 1})
    elif damage == "replay":
        change(
            artifacts,
            expected,
            "00000002-reservation.json",
            {"call": {"turn_id": "turn", "call_id": "1", "tool_name": "exec_command"}},
        )
    elif damage == "uncertain_then_call":
        change(
            artifacts,
            expected,
            "00000001-delivery.json",
            {"status": "uncertain", "error_type": "OSError"},
        )
    else:
        change(artifacts, expected, "authority.json", {"session_id": "other-session"})
    assert not verify_dispatch_journal(artifacts, expected=expected, byte_limit=65536)["ok"]


def test_uncertain_delivery_is_retained_as_uncertain(tmp_path):
    """MON-12: a correctly retained failure is evidence, not an execution attestation."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    path = tmp_path / "dispatch"
    with DispatchBudget(path, session_id="session", ceiling=1) as budget:

        def fail(frame):
            raise OSError("partial write")

        with pytest.raises(OSError):
            budget.dispatch(
                {"turn_id": "turn", "call_id": "1", "tool_name": "exec_command"}, b"x", fail
            )
    artifacts, expected = snapshot(path, attempts=1, reserved=1, ceiling=1)
    result = verify_dispatch_journal(artifacts, expected=expected, byte_limit=65536)
    assert result["ok"] and result["delivery_uncertain"], result
    # A callback failure writes its uncertain primary receipt. It cannot also
    # produce the writer-returned retention-error path for the same attempt.
    name = "00000001-delivery-error.json"
    artifacts[name] = artifacts["00000001-delivery.json"]
    expected["artifacts"][name] = hashlib.sha256(artifacts[name]).hexdigest()
    assert not verify_dispatch_journal(artifacts, expected=expected, byte_limit=65536)["ok"]


def test_whole_journal_obeys_the_callers_byte_limit(tmp_path):
    """MON-8/MON-13: verification respects the caller's declared byte budget."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    artifacts, expected = journal(tmp_path)
    assert not verify_dispatch_journal(artifacts, expected=expected, byte_limit=1)["ok"]


@pytest.mark.parametrize("damage", ["nested_json", "duplicate_field", "missing_delivery"])
def test_incomplete_or_malformed_metadata_fails_closed(tmp_path, monkeypatch, damage):
    """MON-13: even reference-bound malformed evidence cannot abort or pass verification."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    artifacts, expected = journal(tmp_path)
    if damage == "missing_delivery":
        del artifacts["00000001-delivery.json"]
        del expected["artifacts"]["00000001-delivery.json"]
    else:
        name = "authority.json"
        if damage == "nested_json":
            artifacts[name] = b"[" * 3000 + b"0" + b"]" * 3000
            monkeypatch.setattr(json.scanner, "make_scanner", json.scanner.py_make_scanner)
        else:
            artifacts[name] = artifacts[name].replace(b"{", b'{"ceiling":1,', 1)
        expected["artifacts"][name] = hashlib.sha256(artifacts[name]).hexdigest()
    result = verify_dispatch_journal(artifacts, expected=expected, byte_limit=65536)
    assert not result["ok"] and result["errors"], result
