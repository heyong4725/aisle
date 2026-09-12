"""MON-8/MON-12/MON-13: owned child identities and token totals are replayable."""

import pytest

pytestmark = pytest.mark.unit


def event(method, **params):
    return {"method": method, "params": params}


def child_started(scope, child="child"):
    scope.feed(
        event(
            "item/completed",
            threadId="root",
            turnId="root-turn",
            item={
                "type": "subAgentActivity",
                "kind": "started",
                "id": "spawn-call",
                "agentThreadId": child,
                "agentPath": "/root/child",
            },
        )
    )
    scope.feed(event("turn/started", threadId=child, turn={"id": "child-turn"}))


def usage(thread, turn, count):
    value = {
        "inputTokens": count,
        "cachedInputTokens": 0,
        "outputTokens": count,
        "reasoningOutputTokens": 0,
        "totalTokens": 2 * count,
    }
    return event(
        "thread/tokenUsage/updated",
        threadId=thread,
        turnId=turn,
        tokenUsage={"total": value, "last": value},
    )


def test_child_tokens_are_added_once_and_only_root_completion_finishes():
    """MON-12: child totals are separate from root cumulative tokens and never duplicated."""
    from aisle.harness.frontend_app_server import AppServerScope

    scope = AppServerScope("root", "root-turn")
    scope.feed(usage("root", "root-turn", 2))
    child_started(scope)
    scope.feed(usage("child", "child-turn", 1))
    scope.feed(usage("child", "child-turn", 1))
    assert scope.report() == {"tokens": 6, "tokens_generated": 3}
    assert not scope.feed(
        event("turn/completed", threadId="child", turn={"id": "child-turn", "status": "completed"})
    )
    assert scope.feed(
        event("turn/completed", threadId="root", turn={"id": "root-turn", "status": "completed"})
    )


@pytest.mark.parametrize("fault", ["unknown_usage", "unknown_turn", "reused_child"])
def test_child_scope_refuses_unbound_or_incomplete_lifetimes(fault):
    """MON-13: another thread is admitted only through an owned parent spawn event."""
    from aisle.harness.frontend_app_server import AppServerScope

    scope = AppServerScope("root", "root-turn")
    scope.feed(usage("root", "root-turn", 1))
    child_started(scope)
    with pytest.raises(ValueError):
        if fault == "unknown_usage":
            scope.feed(usage("stranger", "stranger-turn", 1))
        elif fault == "unknown_turn":
            scope.feed(event("turn/started", threadId="stranger", turn={"id": "stranger-turn"}))
        else:
            child_started(scope)


def test_root_completion_waits_for_the_owned_child():
    """MON-12: root completion cannot truncate a child's final usage or lifetime."""
    from aisle.harness.frontend_app_server import AppServerScope

    scope = AppServerScope("root", "root-turn")
    scope.feed(usage("root", "root-turn", 2))
    child_started(scope)
    assert not scope.feed(
        event("turn/completed", threadId="root", turn={"id": "root-turn", "status": "completed"})
    )
    scope.feed(usage("child", "child-turn", 1))
    assert scope.feed(
        event("turn/completed", threadId="child", turn={"id": "child-turn", "status": "completed"})
    )
    assert scope.report() == {"tokens": 6, "tokens_generated": 3}


def test_child_completion_notification_is_not_another_tool_call():
    """MON-12: observing task completion must not charge the spawn operation twice."""
    import json

    from aisle.harness.matched_frontend import FrontendToolBudget

    guard = FrontendToolBudget("codex_app_server", 1)
    for phase in ("started", "completed"):
        assert (
            guard(
                json.dumps(
                    event(
                        "item/" + phase,
                        item={"type": "subAgentActivity", "id": "spawn", "kind": "started"},
                    )
                )
            )
            is None
        )
    for phase in ("started", "completed"):
        assert (
            guard(
                json.dumps(
                    event(
                        "item/" + phase,
                        item={
                            "type": "subAgentActivity",
                            "id": "child-finished",
                            "kind": "completed",
                        },
                    )
                )
            )
            is None
        )
    assert guard.report()["observed_calls"] == 1


def test_child_usage_cannot_hide_missing_root_usage():
    """MON-12: child token telemetry cannot make an incomplete session total look final."""
    from aisle.harness.frontend_app_server import AppServerScope

    scope = AppServerScope("root", "root-turn")
    child_started(scope)
    scope.feed(usage("child", "child-turn", 3))
    scope.feed(
        event("turn/completed", threadId="child", turn={"id": "child-turn", "status": "completed"})
    )
    with pytest.raises(ValueError, match="usage"):
        scope.feed(
            event(
                "turn/completed", threadId="root", turn={"id": "root-turn", "status": "completed"}
            )
        )
    assert not scope.complete


@pytest.mark.parametrize("update", [False, True])
def test_child_followup_requires_current_turn_usage_without_double_counting(update):
    """MON-12: a child's previous total is not evidence of accounting for its followup."""
    from aisle.harness.frontend_app_server import AppServerScope

    scope = AppServerScope("root", "root-turn")
    scope.feed(usage("root", "root-turn", 2))
    child_started(scope)
    scope.feed(usage("child", "child-turn", 3))
    scope.feed(
        event("turn/completed", threadId="child", turn={"id": "child-turn", "status": "completed"})
    )
    scope.feed(event("turn/started", threadId="child", turn={"id": "followup"}))
    if update:
        scope.feed(usage("child", "followup", 4))
    scope.feed(
        event("turn/completed", threadId="root", turn={"id": "root-turn", "status": "completed"})
    )
    completion = event(
        "turn/completed", threadId="child", turn={"id": "followup", "status": "completed"}
    )
    if update:
        assert scope.feed(completion)
        assert scope.report() == {"tokens": 12, "tokens_generated": 6}
    else:
        with pytest.raises(ValueError, match="usage"):
            scope.feed(completion)


@pytest.mark.parametrize(
    "method,params",
    [
        ("item/completed", {"item": None}),
        ("turn/started", {"threadId": "child", "turn": []}),
        ("turn/completed", {"threadId": "root", "turn": None}),
        ("thread/tokenUsage/updated", {"threadId": [], "turnId": "root-turn"}),
    ],
)
def test_malformed_scope_notifications_fail_with_protocol_error(method, params):
    """MON-13: malformed scope fields are protocol failures, not unchecked exceptions."""
    from aisle.harness.frontend_app_server import AppServerScope

    scope = AppServerScope("root", "root-turn")
    with pytest.raises(ValueError):
        scope.feed(event(method, **params))
