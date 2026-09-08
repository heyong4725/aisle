"""MON-12: frontend event observations remain distinct from complete tool coverage."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_frontend_tool_lifecycle_counts_one_invocation(agent):
    """MON-12: start/update/completion events do not become independent calls."""
    from aisle.harness.matched_frontend import observe_tools

    if agent == "codex":
        events = [
            {"type": event, "item": {"id": "call-1", "type": "command_execution"}}
            for event in ("item.started", "item.updated", "item.completed")
        ]
    else:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call-1", "content": "files"}
                    ]
                },
            },
        ]
    report = observe_tools(agent, [json.dumps(event) for event in events])
    assert report["ok"] is True, report
    assert report["observed_calls"] == 1
    assert report["completed_calls"] == 1
    assert report["pending_ids"] == []
    assert report["complete_coverage"] is False


def test_codex_completed_only_patch_and_pending_command_are_retained():
    """MON-12: completed-only patches and interrupted commands each retain an identity."""
    from aisle.harness.matched_frontend import observe_tools

    events = [
        {"type": "item.completed", "item": {"id": "patch", "type": "file_change"}},
        {"type": "item.started", "item": {"id": "cmd", "type": "command_execution"}},
        {"type": "item.completed", "item": {"id": "msg", "type": "agent_message"}},
    ]
    report = observe_tools("codex", [json.dumps(event) for event in events])
    assert report["ok"] is True
    assert report["observed_calls"] == 2
    assert report["completed_calls"] == 1
    assert report["pending_ids"] == ["cmd"]


@pytest.mark.parametrize(
    "line", ["not json", '{"type":"item.started","item":{"type":"command_execution"}}']
)
def test_unparseable_or_unidentified_event_cannot_supply_tool_count(line):
    """MON-13: malformed telemetry leaves counts unknown rather than silently reporting zero."""
    from aisle.harness.matched_frontend import observe_tools

    report = observe_tools("codex", [line])
    assert report["ok"] is False
    assert report["observed_calls"] is None
    assert report["error"]


@pytest.mark.parametrize(
    "phases",
    [
        ["item.started", "item.started"],
        ["item.completed", "item.completed"],
        ["item.completed", "item.started"],
        ["item.completed", "item.updated"],
    ],
)
def test_reused_or_reversed_call_identity_cannot_hide_invocations(phases):
    """MON-12/MON-13: conflicting lifecycle records cannot hide extra work."""
    from aisle.harness.matched_frontend import observe_tools

    report = observe_tools(
        "codex",
        [
            json.dumps(
                {
                    "type": phase,
                    "item": {"id": "call-1", "type": "command_execution"},
                }
            )
            for phase in phases
        ],
    )
    assert report["ok"] is False
    assert report["observed_calls"] is None
    assert "lifecycle" in report["error"]


def test_incremental_frontend_budget_counts_unique_calls_and_fails_on_bad_events():
    """MON-8/MON-12: the live guard stops on excess calls and rejects invalid telemetry."""
    from aisle.harness.matched_frontend import FrontendToolBudget

    guard = FrontendToolBudget("codex", 1)
    assert (
        guard(
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "one",
                        "type": "command_execution",
                    },
                }
            )
        )
        is None
    )
    assert (
        guard(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "one",
                        "type": "command_execution",
                    },
                }
            )
        )
        is None
    )
    assert (
        guard(
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "two",
                        "type": "command_execution",
                    },
                }
            )
        )
        == "frontend_tool_budget"
    )
    assert guard.report()["observed_calls"] == 2
    invalid = FrontendToolBudget("codex", 1)
    with pytest.raises(ValueError, match="telemetry"):
        invalid("invalid json")


@pytest.mark.parametrize("fault", [None, "count", "ceiling", "stop"])
def test_live_report_is_reconciled_against_transcript_prefix(fault):
    """MON-12/MON-13: a live budget report must reproduce the guard's observed prefix."""
    from aisle.harness.matched_frontend import FrontendToolBudget, verify_live_report

    lines = [
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": identity,
                    "type": "command_execution",
                },
            }
        )
        for identity in ("one", "two", "buffered-after-stop")
    ]
    guard = FrontendToolBudget("codex", 1)
    for line in lines:
        if guard(line):
            break
    live = guard.report()
    stopped = "frontend_tool_budget"
    if fault == "count":
        live["observed_calls"] = 1
    elif fault == "ceiling":
        live["ceiling"] = 2
    elif fault == "stop":
        stopped = "agent_done"
    if fault is None:
        verify_live_report("codex", 1, lines, live, stopped)
    else:
        with pytest.raises(ValueError, match="live frontend"):
            verify_live_report("codex", 1, lines, live, stopped)


@pytest.mark.parametrize("field", ["ceiling", "observed_calls", "completed_calls"])
def test_live_report_cannot_substitute_booleans_for_counts(field):
    """MON-13: Python boolean/integer equality must not admit a differently typed report."""
    from aisle.harness.matched_frontend import FrontendToolBudget, verify_live_report

    lines = [
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "one",
                    "type": "file_change",
                },
            }
        )
    ]
    guard = FrontendToolBudget("codex", 1)
    guard(lines[0])
    live = guard.report()
    live[field] = True
    with pytest.raises(ValueError, match="live frontend"):
        verify_live_report("codex", 1, lines, live, "agent_done")


@pytest.mark.parametrize("stream_complete", [True, False])
@pytest.mark.parametrize("fault", ["missing_id", "invalid_json", "duplicate", None])
def test_invalid_frontend_events_invalidate_session_without_optional_ceiling(
    tmp_path, fault, stream_complete
):
    """MON-13: malformed recognized telemetry fails closed while raw evidence survives."""
    from test_matched_session import prepared_pair

    from aisle.harness.matched_session import admit_pair, execute_session

    root, candidates, views = prepared_pair(tmp_path)
    plan = admit_pair(root, candidates, views)
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps(
            {
                "schema_version": "aisle.hidden-access-log.v1",
                "adapter_active": True,
                "complete": True,
                "events": [],
            }
        )
    )
    event = {"type": "item.completed", "item": {"id": "call-1", "type": "file_change"}}
    if fault == "missing_id":
        del event["item"]["id"]
    transcript = json.dumps(event) + "\n"
    if fault == "invalid_json":
        transcript = "not JSON\n"
    elif fault == "duplicate":
        transcript *= 2

    def launch(output):
        (output / "session.jsonl").write_text(transcript)
        return {"rc": 0, "stream_complete": stream_complete, "stopped": "agent_done"}

    output = tmp_path / "session"
    result = execute_session(
        plan,
        root,
        views,
        "typed",
        output,
        session_id="frontend-invalid",
        launch=launch,
        hidden_access_log=access,
    )
    assert (output / "session.jsonl").read_text() == transcript
    assert result["artifacts"]["frontend-tools.json"]
    assert result["common_evidence"]["complete"] is False
    if fault is None:
        assert result["ok"], result["error"]
        assert result["frontend_tools"]["observed_calls"] == (1 if stream_complete else None)
        assert result["common_evidence"]["budgets"]["observed"]["tool_calls"] is None
    else:
        assert not result["ok"], result["frontend_tools"]
        assert result["classification"] == "infrastructure_exclusion"
        assert "frontend" in result["error"]
        assert not result["frontend_tools"]["ok"]
        assert result["frontend_tools"]["observed_calls"] is None


@pytest.mark.parametrize("agent", ["codex", "claude"])
@pytest.mark.parametrize("event", [{}, {"type": None}, {"type": ""}, {"type": 1}])
def test_live_guard_refuses_missing_or_invalid_event_discriminator(agent, event):
    """MON-8/MON-13: malformed telemetry cannot silently contribute zero tool calls."""
    from aisle.harness.matched_frontend import FrontendToolBudget

    guard = FrontendToolBudget(agent, 1)
    with pytest.raises(ValueError, match="telemetry"):
        guard(json.dumps(event))
    report = guard.report()
    assert report["ok"] is False
    assert report["observed_calls"] is None
    assert report["complete_coverage"] is False


def test_live_guard_refuses_unknown_item_lifecycle():
    """MON-8/MON-13: an unsupported item event cannot bypass the declared call ceiling."""
    from aisle.harness.matched_frontend import FrontendToolBudget

    guard = FrontendToolBudget("codex", 1)
    with pytest.raises(ValueError, match="telemetry"):
        guard(
            json.dumps(
                {"type": "item.unknown", "item": {"id": "call", "type": "command_execution"}}
            )
        )
    assert guard.report()["observed_calls"] is None


@pytest.mark.parametrize("role", ["assistant", "user"])
@pytest.mark.parametrize("block", [{}, {"type": None}, {"type": ""}, {"type": 1}])
def test_live_guard_refuses_invalid_claude_content_discriminator(role, block):
    """MON-8/MON-13: malformed content blocks cannot be counted as zero calls."""
    from aisle.harness.matched_frontend import FrontendToolBudget

    guard = FrontendToolBudget("claude", 1)
    event = {"type": role, "message": {"content": [block]}}
    with pytest.raises(ValueError, match="telemetry"):
        guard(json.dumps(event))
    assert guard.report()["observed_calls"] is None
    assert guard.report()["complete_coverage"] is False
