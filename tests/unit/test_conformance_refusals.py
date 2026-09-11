"""MON-8/MON-12/MON-13: quota refusal evidence requires replayed reservation decisions."""

import pytest
from test_frontend_dispatch import call
from test_hosted_dispatch import REQUEST, hosted_frame

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("route", ["local", "hosted", "replay", "uncertain"])
def test_only_exhausted_reservations_are_reported_as_budget_refusals(tmp_path, route):
    """MON-13: replay refusal and delivery failure cannot substitute for quota enforcement."""
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        if route == "uncertain":
            with pytest.raises(ValueError):
                budget.hosted_response("request", REQUEST, lambda _: b"invalid stream")
        else:
            budget.dispatch(call(1), b"first", lambda _: None)
            with pytest.raises(DispatchRefused):
                if route == "hosted":
                    budget.hosted_response(
                        "request", REQUEST, lambda _: pytest.fail("upstream called")
                    )
                else:
                    budget.dispatch(
                        call(1 if route == "replay" else 2),
                        b"refused",
                        lambda _: pytest.fail("delivered"),
                    )
    artifacts = {path.name: path.read_bytes() for path in output.iterdir()}
    report = verify_dispatch_journal(artifacts, expected=budget.reference(), byte_limit=100000)
    assert report["ok"], report["errors"]
    expected = []
    if route == "local":
        expected = [{"kind": "local", "reservation": "00000002-reservation.json", "call": call(2)}]
    elif route == "hosted":
        expected = [
            {
                "kind": "hosted",
                "reservation": "hosted-00000001-reservation.json",
                "request_id": "request",
            }
        ]
    assert report["budget_refusals"] == expected
    assert report["complete_coverage"] is False


def test_large_valid_hosted_settlement_can_be_replayed(tmp_path):
    """MON-12: supported bounded provider responses must retain replayable settlement evidence."""
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1000) as budget:
        budget.hosted_response("request", REQUEST, lambda _: hosted_frame(1000))
    artifacts = {path.name: path.read_bytes() for path in output.iterdir()}
    assert len(artifacts["hosted-00000001-settlement.json"]) > 65536
    report = verify_dispatch_journal(
        artifacts, expected=budget.reference(), byte_limit=4 * 1024 * 1024
    )
    assert report["ok"], report["errors"]


def test_hosted_zero_allowance_is_not_quota_proof_with_unspent_budget(tmp_path):
    """MON-8/MON-13: a zero upstream limit cannot masquerade as exhaustion of a larger budget."""
    import hashlib
    import json

    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        budget.dispatch(call(1), b"first", lambda _: None)
        with pytest.raises(DispatchRefused):
            budget.hosted_response("request", REQUEST, lambda _: pytest.fail("upstream called"))
    artifacts = {path.name: path.read_bytes() for path in output.iterdir()}
    reference = budget.reference()
    authority = json.loads(artifacts["authority.json"])
    authority["ceiling"] = reference["ceiling"] = 2
    artifacts["authority.json"] = json.dumps(authority).encode()
    reference["artifacts"]["authority.json"] = hashlib.sha256(
        artifacts["authority.json"]
    ).hexdigest()
    report = verify_dispatch_journal(artifacts, expected=reference, byte_limit=100000)
    assert not report["ok"]
    assert "budget_refusals" not in report
