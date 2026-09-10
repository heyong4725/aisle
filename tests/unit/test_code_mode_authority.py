"""MON-8/MON-12/MON-13: nested callbacks need independent admission before forwarding."""

import json

import pytest

from aisle.harness._code_mode_protocol import message
from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit


def _execution():
    return message(
        "ExecuteRequest",
        session_id="host-session",
        execution_id="execution",
        tool_call_id="outer-call",
        source="await tools.exec_command({cmd:'true'});",
        enabled_tools=[
            {"name": "exec_command", "tool_name": {"name": "exec_command"}, "kind": 1},
            {
                "name": "harness_check",
                "tool_name": {"namespace": "harness", "name": "check"},
                "kind": 1,
            },
        ],
    ).SerializeToString()


def _callback(number=1, *, harness=False, **changes):
    fields = {
        "session_id": "host-session",
        "execution_id": "execution",
        "cell_id": "cell",
        "invocation_id": f"invocation-{number}",
        "runtime_tool_call_id": f"tool-{number}",
        "tool_name": {"namespace": "harness", "name": "check"}
        if harness
        else {"name": "exec_command"},
        "tool_kind": 1,
        "input_json": b"{}" if harness else b'{"cmd":"true"}',
        "sequence": number,
    }
    fields.update(changes)
    return message("ToolCall", **fields).SerializeToString()


def _authority(budget):
    from aisle.harness.code_mode_authority import CodeModeAuthority

    authority = CodeModeAuthority(budget, delegated_tools={"harness.check", "harness.run"})
    authority.open_session("host-session")
    authority.execute(_execution())
    return authority


def test_nested_quota_refuses_before_forwarding_exact_callback(tmp_path):
    """MON-8: a second nested command cannot escape the shared ceiling via one outer cell."""
    forwarded = []
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
        authority = _authority(budget)
        raw = _callback()
        authority.callback(raw, forwarded.append)
        with pytest.raises(DispatchRefused, match="budget"):
            authority.callback(_callback(2), forwarded.append)
    assert forwarded == [raw]
    assert budget.reference()["reserved"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "foreign"},
        {"execution_id": "foreign"},
        {"tool_name": {"name": "undeclared"}},
        {"tool_kind": 2},
        {"sequence": 2},
        {"invocation_id": ""},
        {"runtime_tool_call_id": ""},
        {"input_json": b'{"cmd":"true","cmd":"false"}'},
    ],
)
def test_unbound_callback_cannot_reach_frontend(tmp_path, changes):
    """MON-13: names, wire identities, order and arguments cannot drift after cell admission."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=3) as budget:
        authority = _authority(budget)
        with pytest.raises(ValueError):
            authority.callback(_callback(**changes), lambda _: pytest.fail("callback released"))
    assert budget.reference()["reserved"] == 0


def test_replayed_invocation_under_new_runtime_id_cannot_execute_twice(tmp_path):
    """MON-13: transport invocation identity and runtime identity both resist replay."""
    forwarded = []
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=3) as budget:
        authority = _authority(budget)
        authority.callback(_callback(), forwarded.append)
        with pytest.raises(ValueError, match="replay"):
            authority.callback(_callback(2, invocation_id="invocation-1"), forwarded.append)
    assert len(forwarded) == budget.reference()["reserved"] == 1


@pytest.mark.parametrize("closure", ["cancel", "session"])
def test_early_cancellation_or_lease_closure_prevents_callback_release(tmp_path, closure):
    """MON-8/MON-13: independent control streams may close authority before a callback arrives."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=3) as budget:
        authority = _authority(budget)
        if closure == "cancel":
            authority.cancel("host-session", "invocation-1")
        else:
            authority.close_session("host-session")
        with pytest.raises(ValueError, match="cancel|closed"):
            authority.callback(_callback(), lambda _: pytest.fail("closed callback released"))
    assert budget.reference()["reserved"] == 0


def test_nested_harness_request_is_charged_once_at_existing_controller_boundary(tmp_path):
    """MON-8: nested native and delegated harness calls share a ceiling without double charging."""
    forwarded = []
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.callback(_callback(), forwarded.append)

        def controller_boundary(raw):
            # App Server chooses a new call id; the existing source/grant boundary
            # performs the reservation when the dynamic harness request arrives.
            budget.dispatch(
                {
                    "turn_id": "frontend-turn",
                    "call_id": "new-call-id",
                    "tool_name": "harness.check",
                },
                b"owned App Server request",
                lambda _: forwarded.append(raw),
            )

        authority.callback(_callback(2, harness=True), controller_boundary)
        with pytest.raises(DispatchRefused, match="budget"):
            authority.callback(_callback(3), forwarded.append)
    assert len(forwarded) == budget.reference()["reserved"] == 2
    rows = sorted((tmp_path / "dispatch").glob("*-reservation.json"))
    assert [json.loads(p.read_bytes())["call"]["tool_name"] for p in rows] == [
        "exec_command",
        "harness.check",
        "exec_command",
    ]


def test_flat_tool_name_cannot_impersonate_delegated_harness_namespace(tmp_path):
    """MON-13: a lookalike local name cannot bypass charging as a controller-owned tool."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
        authority = _authority(budget)
        with pytest.raises(ValueError):
            authority.callback(
                _callback(harness=True, tool_name={"name": "harness.check"}),
                lambda _: pytest.fail("lookalike tool delegated without reservation"),
            )


def test_host_sessions_scope_repeated_execution_and_runtime_ids(tmp_path):
    """MON-8/MON-13: legitimate separate host leases share budget without false replay matches."""
    forwarded = []
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.callback(_callback(), forwarded.append)
        authority.open_session("second-host-session")
        execution = message("ExecuteRequest", _execution())
        execution.session_id = "second-host-session"
        authority.execute(execution.SerializeToString())
        authority.callback(_callback(session_id="second-host-session"), forwarded.append)
    assert len(forwarded) == budget.reference()["reserved"] == 2


def _started(**changes):
    fields = {"execution_id": "execution", "cell_id": "cell"}
    fields.update(changes)
    return message("ExecuteEvent", started=fields).SerializeToString()


def test_execution_started_must_agree_with_earlier_callback(tmp_path):
    """MON-13: callbacks may precede start events, but their cell identities must agree."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.callback(_callback(), lambda _: None)
        with pytest.raises(ValueError, match="cell"):
            authority.execution_event("host-session", "execution", _started(cell_id="foreign"))


def test_cell_closure_retires_late_callback_without_charging(tmp_path):
    """MON-8/MON-13: a closure on the lease stream prevents late subscription delivery."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.execution_event("host-session", "execution", _started())
        authority.session_event(
            "host-session",
            message(
                "SessionEvent",
                cell_closed={
                    "execution_id": "execution",
                    "cell_id": "cell",
                    "final_tool_call_sequence": 1,
                },
            ).SerializeToString(),
        )
        with pytest.raises(ValueError, match="closed"):
            authority.callback(_callback(), lambda _: pytest.fail("closed cell released"))
    assert budget.reference()["reserved"] == 0


def test_cell_final_sequence_cannot_erase_observed_callback(tmp_path):
    """MON-12/MON-13: host closure cannot silently discard an observed invocation."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.callback(_callback(), lambda _: None)
        with pytest.raises(ValueError, match="sequence"):
            authority.session_event(
                "host-session",
                message(
                    "SessionEvent",
                    cell_closed={
                        "execution_id": "execution",
                        "cell_id": "cell",
                        "final_tool_call_sequence": 0,
                    },
                ).SerializeToString(),
            )


@pytest.mark.parametrize("invocation", ["invocation-1", "unknown"])
def test_tool_completion_requires_unique_forwarded_invocation(tmp_path, invocation):
    """MON-12/MON-13: completion cannot bind an unknown or already completed invocation."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.callback(_callback(), lambda _: None)
        raw = message(
            "CompleteToolCallRequest",
            session_id="host-session",
            invocation_id=invocation,
            succeeded={"output_json": b"{}"},
        ).SerializeToString()
        if invocation == "invocation-1":
            authority.complete(raw)
        with pytest.raises(ValueError, match="invocation"):
            authority.complete(raw)


def test_execute_stream_requires_one_start_and_one_outcome(tmp_path):
    """MON-12: a transport ending before its outcome is incomplete evidence."""
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=2) as budget:
        authority = _authority(budget)
        authority.execution_event("host-session", "execution", _started())
        with pytest.raises(ValueError, match="incomplete"):
            authority.execution_finished("host-session", "execution")
        authority.execution_event(
            "host-session",
            "execution",
            message(
                "ExecuteEvent", outcome={"cell_id": "cell", "completed": {}}
            ).SerializeToString(),
        )
        authority.execution_finished("host-session", "execution")
