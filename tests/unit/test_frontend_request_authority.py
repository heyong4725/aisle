"""MON-8/MON-12/MON-13: controller-owned call authorizations are single use.

These exercise delegated request authorization, not authentication of an OS
process or complete frontend coverage. Actual adapters must bind trusted calls.
"""

import json

import pytest

pytestmark = pytest.mark.unit


def _request(identity="a" * 32, operation="check"):
    return (
        json.dumps(
            {
                "schema_version": "aisle.matched-tool-request.v1",
                "id": identity,
                "operation": operation,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _authority(tmp_path):
    from aisle.harness.frontend_request_authority import RequestAuthority

    return RequestAuthority(tmp_path / "authority", session_id="session-one")


def test_unissued_request_cannot_claim_a_frontend_identity(tmp_path):
    """MON-12/MON-13: a participant call ID cannot create execution authority."""
    from aisle.harness.frontend_request_authority import RequestRefused

    with _authority(tmp_path) as authority:
        with pytest.raises(RequestRefused):
            authority.consume(_request(), authorization_id="invented")


@pytest.mark.parametrize("change", ["operation", "request_id", "bytes"])
def test_authorization_binds_exact_request_bytes(tmp_path, change):
    """MON-8/MON-13: operation, request identity and byte changes invalidate a grant."""
    from aisle.harness.frontend_request_authority import RequestRefused

    raw = _request()
    with _authority(tmp_path) as authority:
        grant = authority.authorize(
            call={"turn_id": "turn-one", "call_id": "call-one", "tool_name": "harness.check"},
            request=raw,
        )
        altered = {
            "operation": _request(operation="run"),
            "request_id": _request(identity="b" * 32),
            "bytes": raw[:-1] + b" \n",
        }[change]
        with pytest.raises(RequestRefused):
            authority.consume(altered, authorization_id=grant["authorization_id"])


def test_consumed_authorization_cannot_start_a_second_attempt(tmp_path):
    """MON-12/MON-13: one trusted frontend call authorizes at most one request."""
    from aisle.harness.frontend_request_authority import RequestRefused

    raw = _request()
    with _authority(tmp_path) as authority:
        grant = authority.authorize(
            call={"turn_id": "turn-one", "call_id": "call-one", "tool_name": "harness.check"},
            request=raw,
        )
        consumed = authority.consume(raw, authorization_id=grant["authorization_id"])
        assert consumed["session_id"] == "session-one"
        assert consumed["call"] == grant["call"]
        with pytest.raises(RequestRefused):
            authority.consume(raw, authorization_id=grant["authorization_id"])


def test_refusal_is_retained_before_returning_to_caller(tmp_path):
    """MON-12/MON-13: an unauthorized attempt leaves a durable refusal record."""
    from aisle.harness.frontend_request_authority import RequestRefused

    with _authority(tmp_path) as authority:
        with pytest.raises(RequestRefused):
            authority.consume(_request(), authorization_id="invented")
        records = list((tmp_path / "authority").glob("*-refused.json"))
        assert len(records) == 1
        receipt = json.loads(records[0].read_bytes())
        assert receipt["session_id"] == "session-one"
        assert receipt["authorization_id"] == "invented"
        assert receipt["decision"] == "refused"
        assert receipt["request_bytes"] == len(_request())


def test_concurrent_consumers_obtain_only_one_authorization(tmp_path):
    """MON-8/MON-12: concurrent requests cannot multiply a single call grant."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from aisle.harness.frontend_request_authority import RequestRefused

    raw = _request()
    with _authority(tmp_path) as authority:
        grant = authority.authorize(
            call={"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"},
            request=raw,
        )
        barrier = Barrier(2)

        def consume():
            barrier.wait(timeout=5)
            try:
                authority.consume(raw, authorization_id=grant["authorization_id"])
            except RequestRefused:
                return "refused"
            return "authorized"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(consume) for _ in range(2)]
            assert sorted(f.result(timeout=5) for f in futures) == ["authorized", "refused"]
        assert len(list((tmp_path / "authority").glob("*-consumed.json"))) == 1
        assert len(list((tmp_path / "authority").glob("*-refused.json"))) == 1


@pytest.mark.parametrize("stage", ["grant", "consumed", "refused"])
def test_retention_failure_closes_authority(tmp_path, monkeypatch, stage):
    """MON-12/MON-13: uncertain retention cannot release or recover an authorization."""
    from aisle.harness.frontend_request_authority import RequestRefused

    raw = _request()
    call = {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}
    with _authority(tmp_path) as authority:
        grant = authority.authorize(call=call, request=raw) if stage != "grant" else None
        retain = authority._retain

        def fail(name, value):
            if name.endswith(f"-{stage}.json"):
                raise OSError("injected retention failure")
            retain(name, value)

        monkeypatch.setattr(authority, "_retain", fail)
        with pytest.raises(OSError, match="injected retention"):
            if stage == "grant":
                authority.authorize(call=call, request=raw)
            else:
                token = grant["authorization_id"] if stage == "consumed" else "invented"
                authority.consume(raw, authorization_id=token)
        monkeypatch.setattr(authority, "_retain", retain)
        with pytest.raises(RequestRefused, match="closed"):
            authority.authorize(
                call={**call, "call_id": "different"},
                request=_request(identity="b" * 32),
            )


def test_closed_authority_reference_does_not_trust_later_file_contents(tmp_path):
    """MON-12/MON-13: the authority pins durable receipts before audit acquisition."""
    import hashlib

    authority = _authority(tmp_path)
    with authority:
        grant = authority.authorize(
            call={"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"},
            request=_request(),
        )
        authority.consume(_request(), authorization_id=grant["authorization_id"])
        with pytest.raises(ValueError, match="closed"):
            authority.reference()
    expected = authority.reference()
    path = tmp_path / "authority" / (grant["authorization_id"] + "-consumed.json")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b"{}\n")
    assert authority.reference()["artifacts"][path.name] == digest
    expected["artifacts"].clear()
    assert authority.reference()["artifacts"][path.name] == digest


def test_uncertain_retention_cannot_issue_a_successful_reference(tmp_path, monkeypatch):
    """MON-13: failed durable retention leaves no reference that can certify a session."""
    authority = _authority(tmp_path)
    with authority:

        def fail(name, value):
            raise OSError("injected retention failure")

        monkeypatch.setattr(authority, "_retain", fail)
        with pytest.raises(OSError):
            authority.authorize(
                call={"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"},
                request=_request(),
            )
    with pytest.raises(ValueError, match="failed"):
        authority.reference()
