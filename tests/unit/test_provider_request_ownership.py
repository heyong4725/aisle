"""MON-12/MON-13: canonical provider metadata links withheld child work to owned pipe scopes."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case

pytestmark = pytest.mark.unit


def ownership_artifacts():
    records, _ = _case()
    records["00000005-received.json"] = {
        "method": "item/completed",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "item": {
                "type": "subAgentActivity",
                "kind": "started",
                "agentThreadId": "child",
                "agentPath": "/root/child",
            },
        },
    }
    records["00000006-received.json"] = {
        "method": "turn/started",
        "params": {"threadId": "child", "turn": {"id": "child-turn"}},
    }
    records["00000007-received.json"] = {
        "method": "item/started",
        "params": {
            "threadId": "child",
            "turnId": "child-turn",
            "item": {"type": "agentMessage", "id": "message"},
        },
    }
    records["failure.json"] = {"error_type": "ValueError", "error": "provider failed"}
    protocol = {name: json.dumps(value).encode() + b"\n" for name, value in records.items()}
    reference = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 1,
        "stream_complete": False,
        "failure": records["failure.json"],
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in protocol.items()},
    }
    metadata = {
        "session_id": "thread",
        "thread_id": "child",
        "turn_id": "child-turn",
        "parent_thread_id": "thread",
        "parent_turn_id": "turn",
        "root_turn_id": "turn",
        "request_kind": "turn",
    }
    flat = {key: value for key, value in metadata.items() if key != "request_kind"}
    flat["x-codex-parent-thread-id"] = flat.pop("parent_thread_id")
    flat["x-codex-turn-metadata"] = json.dumps(metadata)
    frame = (
        b'event: response.output_item.done\ndata: {"type'
        b'":"response.output_item.done","item":{"type":'
        b'"function_call","call_id":"child-call","name"'
        b':"exec_command","arguments":"{}"}}\n\n'
    )
    artifacts = {"frontend-protocol/" + name: raw for name, raw in protocol.items()}
    artifacts.update(
        {
            "frontend-protocol-reference.json": json.dumps(reference).encode(),
            "frontend-dispatch/00000004.frame": frame,
            "frontend-dispatch/00000004-reservation.json": json.dumps(
                {"call": {"call_id": "child-call"}}
            ).encode(),
            "provider/00000005-response.sse": frame,
            "provider/00000005-request.json": b'{"content_encoding":"identity"}',
            "provider/00000005-request.body": json.dumps({"client_metadata": flat}).encode(),
        }
    )
    return artifacts, flat, metadata


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "foreign_thread",
        "foreign_turn",
        "parent",
        "root",
        "flat",
        "missing",
        "duplicate_response",
        "call",
        "kind",
    ],
)
def test_request_owner_requires_canonical_metadata_and_owned_ancestry(drift):
    """MON-13: request content or a child label alone cannot attribute a refused effect."""
    from aisle.harness.frontend_qualification import provider_call_owner

    artifacts, flat, metadata = ownership_artifacts()
    if drift == "foreign_thread":
        metadata["thread_id"] = flat["thread_id"] = "foreign"
    elif drift == "foreign_turn":
        metadata["turn_id"] = flat["turn_id"] = "foreign"
    elif drift == "parent":
        metadata["parent_thread_id"] = flat["x-codex-parent-thread-id"] = "foreign"
    elif drift == "root":
        metadata["root_turn_id"] = flat["root_turn_id"] = "other"
    elif drift == "flat":
        flat["turn_id"] = "other"
    elif drift == "kind":
        metadata["request_kind"] = "compact"
    elif drift == "duplicate_response":
        artifacts["provider/00000006-response.sse"] = artifacts["provider/00000005-response.sse"]
    elif drift == "call":
        artifacts["frontend-dispatch/00000004-reservation.json"] = b'{"call":{"call_id":"other"}}'
    flat["x-codex-turn-metadata"] = json.dumps(metadata)
    if drift == "missing":
        del flat["x-codex-turn-metadata"]
    artifacts["provider/00000005-request.body"] = json.dumps({"client_metadata": flat}).encode()
    if drift is None:
        assert provider_call_owner(artifacts, attempt=4) == {
            "request_id": "00000005",
            "thread_id": "child",
            "turn_id": "child-turn",
            "parent_thread_id": "thread",
            "parent_turn_id": "turn",
        }
    else:
        with pytest.raises(ValueError):
            provider_call_owner(artifacts, attempt=4)


@pytest.mark.parametrize("drift", [None, "child", "turn", "path", "work", "spawn"])
def test_late_child_completion_notification_preserves_owned_ancestry(drift):
    """MON-13: child status may follow parent completion, but cannot authorize late work or
    spawning.
    """
    from aisle.harness.frontend_qualification import provider_call_owner

    artifacts, _, _ = ownership_artifacts()
    parent_done = {
        "method": "turn/completed",
        "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
    }
    late = {
        "method": "item/started",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "item": {
                "type": "subAgentActivity",
                "kind": "completed",
                "agentThreadId": "child",
                "agentPath": "/root/child",
            },
        },
    }
    if drift == "child":
        late["params"]["item"]["agentThreadId"] = "foreign"
    elif drift == "turn":
        late["params"]["turnId"] = "other"
    elif drift == "path":
        late["params"]["item"]["agentPath"] = "/root/other"
    elif drift == "work":
        late["params"]["item"]["type"] = "commandExecution"
    elif drift == "spawn":
        late["params"]["item"]["kind"] = "started"
    artifacts["frontend-protocol/00000007-received.json"] = json.dumps(parent_done).encode() + b"\n"
    artifacts["frontend-protocol/00000008-received.json"] = json.dumps(late).encode() + b"\n"
    reference = json.loads(artifacts["frontend-protocol-reference.json"])
    reference["artifacts"] = {
        name.removeprefix("frontend-protocol/"): hashlib.sha256(raw).hexdigest()
        for name, raw in artifacts.items()
        if name.startswith("frontend-protocol/")
    }
    artifacts["frontend-protocol-reference.json"] = json.dumps(reference).encode()
    if drift is None:
        assert provider_call_owner(artifacts, attempt=4)["thread_id"] == "child"
    else:
        with pytest.raises(ValueError):
            provider_call_owner(artifacts, attempt=4)
