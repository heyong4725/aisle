"""MON-12/MON-13: nested completion results bind callbacks to actual controller attempts."""

import copy
import json

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(params=[False, True], ids=["small", "large-worker-index"])
def worker_result(request):
    if not request.param:
        return {}
    return {
        "worker_evidence": {
            "schema_version": "aisle.monolithic-worker-retention.v1",
            "ok": True,
            "errors": [],
            "worker_evidence_present": True,
            "eligible_for_estimate": False,
            "files": {f"frame-{i}.json": "a" * 64 for i in range(2000)},
        }
    }


@pytest.mark.parametrize(
    "drift", [None, "arguments", "request", "result", "pipe", "reference", "missing", "duplicate"]
)
def test_nested_completion_requires_exact_pipe_and_controller_result(drift, worker_result):
    """MON-13: a shared tool name cannot substitute for a unique audited request/result chain."""
    from aisle.harness.frontend_qualification import link_nested_harness_results

    call = {"turn_id": "turn", "call_id": "frontend-call", "tool_name": "harness.check"}
    attempt = {
        "immutable_id": "attempt-id",
        "attempt": 1,
        "operation": "check",
        "ok": True,
        "classification": "tool_result",
        "result": {"ok": True, **worker_result},
        "error": None,
    }
    response = {key: attempt[key] for key in ("ok", "classification", "result", "error", "attempt")}
    response["request_id"] = "a" * 32
    from aisle.harness.matched_app_server import controller_reply

    text = controller_reply(response, request_id="a" * 32)["contentItems"][0]["text"]
    callback = {
        "session_id": "host",
        "invocation_id": "invocation",
        "runtime_call_id": "runtime-call",
        "tool_name": "harness.check",
        "arguments": {},
        "output_json": json.dumps(text),
    }
    source = json.dumps({"params": {"arguments": {}}}).encode()
    prefix = {
        "calls": [(7, call, source)],
        "sent": [
            {
                "id": 7,
                "result": {"success": True, "contentItems": [{"type": "inputText", "text": text}]},
            }
        ],
    }
    link = {
        "request_id": "a" * 32,
        "attempt": 1,
        "attempt_id": "attempt-id",
        "frontend_authorization": {"call": call},
    }
    artifacts = {
        "tool-request-index.jsonl": json.dumps(link).encode() + b"\n",
        "tool-000001/attempt.json": json.dumps(attempt).encode(),
    }
    callbacks = [callback]
    if drift == "arguments":
        callback["arguments"] = {"other": True}
    elif drift in {"request", "result"}:
        changed = copy.deepcopy(response)
        changed["request_id" if drift == "request" else "result"] = (
            "b" * 32 if drift == "request" else {"ok": False}
        )
        callback["output_json"] = json.dumps(json.dumps(changed))
        if drift == "result":
            # Even a matching host/pipe forgery cannot rewrite the controller attempt.
            prefix["sent"][0]["result"]["contentItems"][0]["text"] = json.dumps(changed)
    elif drift == "reference":
        changed = json.loads(text)
        changed["result"]["worker_evidence"] = {
            "schema_version": "aisle.monolithic-worker-reference.v1",
            "sha256": "b" * 64,
        }
        forged = json.dumps(changed)
        callback["output_json"] = json.dumps(forged)
        prefix["sent"][0]["result"]["contentItems"][0]["text"] = forged
    elif drift == "pipe":
        prefix["sent"][0]["result"]["success"] = False
    elif drift == "missing":
        prefix["sent"] = []
    elif drift == "duplicate":
        callbacks.append(copy.deepcopy(callback))
    if drift is None:
        assert link_nested_harness_results(artifacts, prefix=prefix, callbacks=callbacks) == [
            {
                "session_id": "host",
                "invocation_id": "invocation",
                "runtime_call_id": "runtime-call",
                "call_id": "frontend-call",
                "turn_id": "turn",
                "request_id": "a" * 32,
                "attempt_id": "attempt-id",
            }
        ]
    else:
        with pytest.raises(ValueError):
            link_nested_harness_results(artifacts, prefix=prefix, callbacks=callbacks)


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "namespace",
        "arguments",
        "request",
        "result",
        "reference",
        "error_flag",
        "missing_source",
        "missing_completion",
        "completion_result",
        "completion_status",
        "duplicate",
    ],
)
@pytest.mark.parametrize("ok", [True, False])
def test_nested_mcp_completion_binds_owned_source_and_controller(drift, ok, worker_result):
    """MON-12/MON-13: nested MCP joins preserve namespace, ownership and exact controller
    results.
    """
    from aisle.harness.frontend_qualification import link_nested_harness_results

    call = {"turn_id": "turn", "call_id": "frontend-call", "tool_name": "harness.check"}
    attempt = {
        "immutable_id": "attempt-id",
        "attempt": 1,
        "operation": "check",
        "ok": ok,
        "classification": "tool_result",
        "result": {"ok": ok, **worker_result},
        "error": None,
    }
    response = {key: attempt[key] for key in ("ok", "classification", "result", "error", "attempt")}
    response["request_id"] = "a" * 32
    from aisle.harness.matched_app_server import controller_reply

    text = controller_reply(response, request_id="a" * 32)["contentItems"][0]["text"]
    content = [{"type": "text", "text": text}]
    output = {"content": content, "isError": not ok}
    callback = {
        "session_id": "host",
        "invocation_id": "invocation",
        "runtime_call_id": "runtime-call",
        "tool_name": "mcp__aisle_harness.check",
        "arguments": {},
        "output_json": json.dumps(output),
    }
    item = {
        "type": "mcpToolCall",
        "id": "frontend-call",
        "server": "aisle_harness",
        "tool": "check",
        "arguments": {},
        "status": "inProgress",
    }
    key = ("turn", "frontend-call")
    prefix = {
        "calls": [],
        "sent": [],
        "mcp_sources": {
            key: json.dumps(
                {"params": {"threadId": "thread", "turnId": "turn", "item": item}}
            ).encode()
        },
        "mcp_completions": {
            key: {
                **item,
                "status": "completed" if ok else "failed",
                "error": None,
                "result": {
                    "content": copy.deepcopy(content),
                    "structuredContent": None,
                    "_meta": None,
                },
            }
        },
    }
    link = {
        "request_id": "a" * 32,
        "attempt": 1,
        "attempt_id": "attempt-id",
        "frontend_authorization": {"call": call},
    }
    artifacts = {
        "tool-request-index.jsonl": json.dumps(link).encode() + b"\n",
        "tool-000001/attempt.json": json.dumps(attempt).encode(),
    }
    callbacks = [callback]
    if drift == "namespace":
        callback["tool_name"] = "mcp__foreign.check"
    elif drift == "arguments":
        callback["arguments"] = {"extra": True}
    elif drift in {"request", "result"}:
        response["request_id" if drift == "request" else "result"] = (
            "b" * 32 if drift == "request" else {"ok": not ok}
        )
        output["content"][0]["text"] = json.dumps(response)
        # Matching host/frontend forgeries must still disagree with the controller.
        prefix["mcp_completions"][key]["result"]["content"] = copy.deepcopy(output["content"])
        callback["output_json"] = json.dumps(output)
    elif drift == "reference":
        changed = json.loads(text)
        changed["result"]["worker_evidence"] = {
            "schema_version": "aisle.monolithic-worker-reference.v1",
            "sha256": "b" * 64,
        }
        output["content"][0]["text"] = json.dumps(changed)
        prefix["mcp_completions"][key]["result"]["content"] = copy.deepcopy(output["content"])
        callback["output_json"] = json.dumps(output)
    elif drift == "error_flag":
        output["isError"] = ok
        callback["output_json"] = json.dumps(output)
    elif drift == "missing_source":
        prefix["mcp_sources"].clear()
    elif drift == "missing_completion":
        prefix["mcp_completions"].clear()
    elif drift == "completion_result":
        prefix["mcp_completions"][key]["result"]["content"][0]["text"] = "{}"
    elif drift == "completion_status":
        prefix["mcp_completions"][key]["status"] = "failed" if ok else "completed"
    elif drift == "duplicate":
        callbacks.append(copy.deepcopy(callback))
    if drift is None:
        assert link_nested_harness_results(artifacts, prefix=prefix, callbacks=callbacks) == [
            {
                "session_id": "host",
                "invocation_id": "invocation",
                "runtime_call_id": "runtime-call",
                "call_id": "frontend-call",
                "turn_id": "turn",
                "request_id": "a" * 32,
                "attempt_id": "attempt-id",
            }
        ]
    else:
        with pytest.raises(ValueError):
            link_nested_harness_results(artifacts, prefix=prefix, callbacks=callbacks)
