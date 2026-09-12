"""MON-12/MON-13: malformed controller results must not become frontend success."""

import copy
import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ok", [True, False])
def test_large_worker_index_uses_authenticated_frontend_reference(ok):
    """MON-12/MON-13: a full private worker index need not overflow the frontend reply."""
    import hashlib

    from aisle.harness.frontend_app_server import MAX_MESSAGE_BYTES
    from aisle.harness.matched_app_server import controller_reply

    evidence = {
        "schema_version": "aisle.monolithic-worker-retention.v1",
        "ok": ok,
        "errors": [] if ok else ["worker failed"],
        "worker_evidence_present": True,
        "eligible_for_estimate": False,
        "files": {f"frame-{i}": "a" * 64 for i in range(2000)},
    }
    response = {
        "ok": ok,
        "classification": "tool_result",
        "attempt": 1,
        "result": {"ok": ok, "episodes": [], "worker_evidence": evidence},
        "error": None,
        "request_id": "a" * 32,
    }
    before = copy.deepcopy(response)
    reply = controller_reply(response, request_id="a" * 32)
    visible = json.loads(reply["contentItems"][0]["text"])
    reference = visible["result"]["worker_evidence"]
    assert reference["schema_version"] == "aisle.monolithic-worker-reference.v1"
    assert (
        reference["sha256"]
        == hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    assert reference["file_count"] == 2000
    assert reference["ok"] is ok and reference["errors"] == evidence["errors"]
    assert visible["ok"] is ok and reply["success"] is ok
    assert len(json.dumps(reply).encode()) <= MAX_MESSAGE_BYTES
    assert response == before


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "denied",
        "infrastructure",
        "ok",
        "identity",
        "attempt",
        "classification",
        "result",
        "extra",
        "error",
        "oversize",
    ],
)
def test_controller_reply_validates_identity_and_preserves_denial(fault):
    """MON-13: reject malformed/foreign responses and preserve valid negative tool outcomes."""
    from aisle.harness.matched_app_server import controller_reply

    response = {
        "ok": True,
        "classification": "tool_result",
        "attempt": 1,
        "result": {"ok": True},
        "error": None,
        "request_id": "a" * 32,
    }
    if fault == "denied":
        response.update(ok=False, result={"ok": False})
    elif fault == "infrastructure":
        response.update(
            ok=False, classification="infrastructure_exclusion", result=None, error="unavailable"
        )
    elif fault == "ok":
        response["ok"] = "false"
    elif fault == "identity":
        response["request_id"] = "b" * 32
    elif fault == "attempt":
        response["attempt"] = True
    elif fault == "classification":
        response["classification"] = "unknown"
    elif fault == "result":
        response["result"]["ok"] = False
    elif fault == "extra":
        response["untrusted"] = True
    elif fault == "error":
        response["error"] = {"bad": True}
    elif fault == "oversize":
        response["result"]["message"] = "x" * 65536
    before = copy.deepcopy(response)
    if fault in {None, "denied", "infrastructure"}:
        reply = controller_reply(response, request_id="a" * 32)
        assert reply["success"] is response["ok"]
        assert json.loads(reply["contentItems"][0]["text"]) == response
    else:
        with pytest.raises(ValueError, match="controller response"):
            controller_reply(response, request_id="a" * 32)
    assert response == before


def test_adapter_rejects_malformed_response_before_returning_to_frontend(tmp_path, monkeypatch):
    """MON-12/MON-13: malformed responses leave the reservation charged and no frontend reply."""
    from pathlib import Path

    from test_matched_tools import _controller

    from aisle.harness import matched_app_server as runner
    from aisle.harness.matched_tool_service import ToolService

    controller, _, output = _controller(tmp_path, "typed")
    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    monkeypatch.setattr(ToolService, "_serve", lambda service: service.stop.wait())
    monkeypatch.setattr(
        ToolService,
        "controller_response",
        lambda service, identity: {
            "request_id": identity,
            "ok": "false",
            "classification": "tool_result",
            "result": {"ok": False},
            "error": None,
            "attempt": 1,
        },
    )

    def frontend(*args, handle_call, **kwargs):
        handle_call(
            {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}, b"owned source"
        )
        pytest.fail("malformed result reached the frontend")

    monkeypatch.setattr(runner, "run_app_server", frontend)
    with pytest.raises(ValueError, match="controller response"):
        runner.run_authorized_app_server(
            controller,
            ["unused"],
            cwd=tmp_path,
            env={},
            launch={"app_server": {"baseInstructions": "system", "developerInstructions": "task"}},
            budget={
                **controller.plan["arms"]["typed"]["budget"],
                "wall_ceiling_s": 2,
                "frontend_tool_ceiling": 1,
            },
            references={},
        )
    assert controller.attempts == 0
    delivery = json.loads((output / "frontend-dispatch/00000001-delivery.json").read_bytes())
    assert delivery["status"] == "uncertain" and delivery["error_type"] == "ValueError"
    reference = json.loads((output / "frontend-dispatch-reference.json").read_bytes())
    assert reference["reserved"] == 1
    authority = json.loads((output / "frontend-authority-reference.json").read_bytes())
    failures = list((output / "frontend-authority").glob("*-response-rejected.json"))
    assert len(failures) == 1
    import hashlib

    assert (
        authority["artifacts"][failures[0].name]
        == hashlib.sha256(failures[0].read_bytes()).hexdigest()
    )
    rejected = json.loads(failures[0].read_bytes())
    assert rejected["response"]["ok"] == "false"
    assert rejected["request_id"] == rejected["response"]["request_id"]
    assert rejected["call"] == {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}
