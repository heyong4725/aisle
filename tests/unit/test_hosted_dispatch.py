"""MON-8/MON-12/MON-13: hosted allowances precede upstream work and share local budget."""

import json

import pytest
from test_frontend_dispatch import call
from test_provider_response_authority import frames

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit
REQUEST = json.dumps(
    {"model": "fixture", "stream": True, "tools": [{"type": "web_search"}]}
).encode()


def hosted_frame(count, response_id="response"):
    return frames(
        [
            {
                "type": "web_search_call",
                "id": f"search-{i}",
                "status": "completed",
                "action": {"type": "search", "query": "fixture"},
            }
            for i in range(count)
        ],
        response_id=response_id,
    )


@pytest.mark.parametrize("actual", [0, 1, 2])
def test_allowance_is_durable_before_upstream_and_unused_slots_remain_available(tmp_path, actual):
    """MON-12: model requests do not consume tools; only completed hosted calls settle."""
    output = tmp_path / "budget"
    effects = []
    with DispatchBudget(output, session_id="session", ceiling=4) as budget:
        budget.dispatch(call(1), b"local-before", effects.append)

        def exchange(request):
            assert json.loads(request)["max_tool_calls"] == 3
            reservation = json.loads((output / "hosted-00000001-reservation.json").read_bytes())
            assert reservation["allowance"] == 3
            assert reservation["reserved_before"] == 1
            assert (output / "hosted-00000001-request.frame").read_bytes() == request
            return hosted_frame(actual)

        assert budget.hosted_response("request-1", REQUEST, exchange) == hosted_frame(actual)
        for i in range(3 - actual):
            budget.dispatch(call(i + 2), b"local-after", effects.append)
        with pytest.raises(DispatchRefused, match="exhausted"):
            budget.dispatch(call(20), b"excess", effects.append)
    assert len(effects) == 4 - actual
    assert budget.reference()["reserved"] == 4


@pytest.mark.parametrize("fault", ["network", "malformed", "excess"])
def test_uncertain_hosted_work_keeps_full_allowance_and_retires_authority(tmp_path, fault):
    """MON-13: timeout, malformed completion or provider limit breach never refunds work."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=2) as budget:

        def exchange(_):
            if fault == "network":
                raise OSError("connection lost")
            return b"broken" if fault == "malformed" else hosted_frame(3)

        with pytest.raises((ValueError, OSError)):
            budget.hosted_response("request-1", REQUEST, exchange)
        with pytest.raises(DispatchRefused, match="closed"):
            budget.dispatch(call(1), b"no", lambda _: pytest.fail("retired budget delivered"))
    assert budget.reference()["reserved"] == 2


def test_exhausted_budget_does_not_send_another_hosted_request(tmp_path):
    """MON-8: an exhausted shared ceiling refuses before hosted side effects can start."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:
        budget.dispatch(call(1), b"local", lambda _: None)
        with pytest.raises(DispatchRefused, match="exhausted"):
            budget.hosted_response("request-1", REQUEST, lambda _: pytest.fail("sent upstream"))


def test_hosted_transaction_replays_with_local_calls_and_detects_rewritten_counts(tmp_path):
    """MON-13: offline replay recomputes hosted work instead of trusting settlement counts."""
    import hashlib

    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    output = tmp_path / "budget"
    with DispatchBudget(output, session_id="session", ceiling=3) as budget:
        budget.dispatch(call(1), b"local", lambda _: None)
        budget.hosted_response("request-1", REQUEST, lambda _: hosted_frame(1))
        budget.dispatch(call(2), b"local", lambda _: None)
    artifacts = {path.name: path.read_bytes() for path in output.iterdir()}
    reference = budget.reference()
    assert verify_dispatch_journal(artifacts, expected=reference, byte_limit=100000)["ok"]
    name = "hosted-00000001-settlement.json"
    row = json.loads(artifacts[name])
    row["calls"] = []
    artifacts[name] = json.dumps(row).encode()
    reference["artifacts"][name] = hashlib.sha256(artifacts[name]).hexdigest()
    assert not verify_dispatch_journal(artifacts, expected=reference, byte_limit=100000)["ok"]


@pytest.mark.parametrize("suffix", ["-response.frame", "-settlement.json"])
def test_hosted_retention_failure_cannot_produce_a_valid_reference(tmp_path, monkeypatch, suffix):
    """MON-13: even a recovered failure receipt cannot erase failed durable evidence."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=2) as budget:
        retain = budget._retain
        failed = []

        def fail_once(name, value):
            if name.endswith(suffix) and not failed:
                failed.append(True)
                raise OSError("retention fixture")
            return retain(name, value)

        monkeypatch.setattr(budget, "_retain", fail_once)
        with pytest.raises(OSError):
            budget.hosted_response("request-1", REQUEST, lambda _: hosted_frame(1))
    with pytest.raises(DispatchRefused, match="retention"):
        budget.reference()


def test_hosted_and_local_concurrent_calls_cannot_spend_the_same_slot(tmp_path):
    """MON-8: a pending upstream allowance excludes concurrent local execution."""
    import concurrent.futures
    import threading

    entered, finish, local_started = threading.Event(), threading.Event(), threading.Event()
    effects = []
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:

        def exchange(_):
            entered.set()
            assert finish.wait(5)
            return hosted_frame(1)

        def local():
            local_started.set()
            with pytest.raises(DispatchRefused, match="exhausted"):
                budget.dispatch(call(1), b"local", effects.append)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(budget.hosted_response, "request-1", REQUEST, exchange)
            try:
                assert entered.wait(5)
                second = pool.submit(local)
                assert local_started.wait(5)
            finally:
                finish.set()
            first.result(timeout=5)
            second.result(timeout=5)
    assert effects == []
