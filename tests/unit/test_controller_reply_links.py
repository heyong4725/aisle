"""MON-13: join owned frontend replies to already-audited service and attempt records."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize("drift", [None, "success", "request", "result", "missing", "duplicate"])
def test_controller_reply_must_match_audited_attempt(drift, large):
    """MON-13: valid reply shape alone cannot establish the controller-to-frontend result link."""
    from aisle.harness.frontend_qualification import match_controller_replies
    from aisle.harness.matched_app_server import controller_reply

    call = {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}
    response = {
        "ok": False,
        "classification": "tool_result",
        "result": {"ok": False},
        "error": None,
        "attempt": 1,
        "request_id": "a" * 32,
    }
    if large:
        response["result"]["worker_evidence"] = {
            "schema_version": "aisle.monolithic-worker-retention.v1",
            "ok": True,
            "errors": [],
            "worker_evidence_present": True,
            "eligible_for_estimate": False,
            "files": {f"frame-{i}.json": "a" * 64 for i in range(2000)},
        }
    attempt = {**response, "immutable_id": "attempt-id", "operation": "check"}
    link = {
        "attempt_id": "attempt-id",
        "request_id": "a" * 32,
        "frontend_authorization": {"call": call},
    }
    artifacts = {
        "tool-events.jsonl": json.dumps({"record": attempt}).encode(),
        "tool-request-index.jsonl": json.dumps(link).encode(),
    }
    reply = {"id": 7, "result": controller_reply(response, request_id="a" * 32)}
    sent = [{}, {}, {}, {}, reply]
    if drift == "success":
        reply["result"]["success"] = True
    elif drift in {"request", "result"}:
        value = dict(response)
        value["request_id" if drift == "request" else "result"] = (
            "wrong" if drift == "request" else {"ok": True}
        )
        reply["result"]["contentItems"][0]["text"] = json.dumps(value)
    elif drift == "missing":
        sent.pop()
    elif drift == "duplicate":
        sent.append(reply)
    prefix = {"calls": [(7, call, b"owned")], "sent": sent}
    if drift is None:
        assert match_controller_replies(artifacts, prefix) == 1
    else:
        with pytest.raises(ValueError):
            match_controller_replies(artifacts, prefix)
