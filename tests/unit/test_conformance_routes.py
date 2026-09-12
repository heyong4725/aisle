"""MON-12/MON-13: route observations distinguish actual completion from mere delivery."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "route,kind,name",
    [
        ("native", "commandExecution", "exec_command"),
        ("native_edit", "fileChange", "apply_patch"),
        ("mcp", "mcpToolCall", "record"),
    ],
)
@pytest.mark.parametrize("fault", [None, "failed", "unmatched", "scope"])
def test_route_completion_requires_the_admitted_call_and_owned_scope(route, kind, name, fault):
    """MON-13: classification after source replay cannot turn a failed/unmatched item into
    availability.
    """
    from aisle.harness.frontend_qualification import completed_route_items

    item = {
        "id": "call",
        "type": kind,
        "status": "completed",
        "exitCode": 0,
        "changes": [{"path": "marker", "diff": "changed"}],
        "server": "fixture",
        "tool": name,
        "error": None,
        "result": {"content": [{"type": "text", "text": "done"}]},
    }
    if fault == "failed":
        item["status"] = "failed"
    elif fault == "unmatched":
        item["id"] = "other"
    events = [
        {
            "method": "item/completed",
            "params": {
                "threadId": "other" if fault == "scope" else "thread",
                "turnId": "turn",
                "item": item,
            },
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
        },
    ]
    namespace = "mcp__fixture" if route == "mcp" else None
    source = {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "type": "function_call",
            "id": "item",
            "call_id": "call",
            "name": name,
            "namespace": namespace,
            "arguments": "{}",
        },
    }
    artifacts = {
        "frontend-protocol-reference.json": json.dumps(
            {"thread_id": "thread", "turn_id": "turn"}
        ).encode(),
        "frontend-dispatch-reference.json": b'{"attempts":1}',
        "frontend-dispatch/00000001-reservation.json": json.dumps(
            {
                "decision": "authorized",
                "call": {
                    "turn_id": "provider",
                    "call_id": "call",
                    "tool_name": json.dumps([namespace, name, "function_call"]),
                },
            }
        ).encode(),
        "frontend-dispatch/00000001.frame": f"data: {json.dumps(source)}\n\n".encode(),
        "tool-events.jsonl": b"",
    }
    artifacts.update(
        {
            f"frontend-protocol/{i:08d}-received.json": json.dumps(event).encode()
            for i, event in enumerate(events, 1)
        }
    )
    if fault == "scope":
        with pytest.raises(ValueError):
            completed_route_items(artifacts)
    else:
        routes = completed_route_items(artifacts)
        assert bool(routes[route]) is (fault is None)
        assert all(not rows for key, rows in routes.items() if key != route)
