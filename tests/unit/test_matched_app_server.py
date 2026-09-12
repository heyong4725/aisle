"""MON-12/MON-13: preserve primary failures in the shared App Server runner."""

import pytest
from test_matched_tools import _controller

pytestmark = pytest.mark.unit


def test_cleanup_retention_failure_preserves_frontend_failure(tmp_path, monkeypatch):
    """MON-12: failed report retention must not replace the original frontend failure."""
    from aisle.harness import matched_app_server as runner

    controller, _, _ = _controller(tmp_path, "typed")

    def fail_frontend(*args, **kwargs):
        raise ValueError("original frontend failure")

    def fail_retention(*args, **kwargs):
        raise OSError("secondary report retention failure")

    monkeypatch.setattr(runner, "run_app_server", fail_frontend)
    monkeypatch.setattr(runner, "_json", fail_retention)
    from pathlib import Path

    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    with pytest.raises(ValueError, match="original frontend failure") as caught:
        runner.run_authorized_app_server(
            controller,
            ["unused"],
            cwd=tmp_path,
            env={},
            launch={
                "app_server": {
                    "baseInstructions": "system prompt",
                    "developerInstructions": "research contract",
                }
            },
            budget={
                **controller.plan["arms"]["typed"]["budget"],
                "wall_ceiling_s": 1,
                "frontend_tool_ceiling": 1,
            },
            references={},
        )
    assert any("secondary report retention" in note for note in caught.value.__notes__)


def test_failed_frontend_retains_closed_authority_and_partial_protocol(tmp_path, monkeypatch):
    """MON-12/MON-13: failed sessions retain controller references without inventing completion."""
    import json
    from pathlib import Path

    from aisle.harness import matched_app_server as runner

    controller, _, output = _controller(tmp_path, "typed")
    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    partial = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 0,
        "artifacts": {},
        "stream_complete": False,
        "failure": {"error_type": "ValueError", "error": "frontend failed"},
    }

    def frontend(*args, **kwargs):
        kwargs["on_reference"](partial)
        raise ValueError("frontend failed")

    monkeypatch.setattr(runner, "run_app_server", frontend)
    references = {}
    with pytest.raises(ValueError, match="frontend failed"):
        runner.run_authorized_app_server(
            controller,
            ["unused"],
            cwd=tmp_path,
            env={},
            launch={"app_server": {"baseInstructions": "system", "developerInstructions": "task"}},
            budget={**controller.plan["arms"]["typed"]["budget"], "wall_ceiling_s": 1},
            references=references,
        )
    assert json.loads((output / "frontend-protocol-reference.json").read_bytes()) == partial
    assert (
        json.loads((output / "frontend-authority-reference.json").read_bytes())
        == references["authority"]
    )
    assert references["authority"]["session_id"] == controller.session_id


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("failure", ["ceiling", "retention"])
def test_source_calls_reserve_before_grant_or_controller(tmp_path, monkeypatch, arm, failure):
    """MON-8/MON-12/MON-13: missing item notifications cannot bypass source-call admission."""
    import json
    from pathlib import Path

    from aisle.harness import matched_app_server as runner
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    controller, _, output = _controller(tmp_path, arm)
    channel = Path(controller.plan["ambient_bindings"][arm]["environment"]["HOME"]) / "tool-channel"
    channel.mkdir()
    if failure == "retention":
        retain = DispatchBudget._retain

        def unavailable(authority, name, value):
            if name.endswith("-reservation.json"):
                raise OSError("reservation storage unavailable")
            return retain(authority, name, value)

        monkeypatch.setattr(DispatchBudget, "_retain", unavailable)

    def frontend(*args, handle_call, **kwargs):
        for number in (1, 2):
            call = {"turn_id": "turn", "call_id": str(number), "tool_name": "harness.check"}
            source = (
                json.dumps(
                    {
                        "id": number,
                        "method": "item/tool/call",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "callId": str(number),
                            "namespace": "harness",
                            "tool": "check",
                            "arguments": {},
                        },
                    }
                ).encode()
                + b"\n"
            )
            handle_call(call, source)
        raise AssertionError("excess source call reached the controller")

    monkeypatch.setattr(runner, "run_app_server", frontend)
    expected = DispatchRefused if failure == "ceiling" else OSError
    with pytest.raises(expected, match="budget|reservation storage"):
        runner.run_authorized_app_server(
            controller,
            ["unused"],
            cwd=tmp_path,
            env={},
            launch={"app_server": {"baseInstructions": "system", "developerInstructions": "task"}},
            budget={
                **controller.plan["arms"][arm]["budget"],
                "wall_ceiling_s": 10,
                "frontend_tool_ceiling": 1,
            },
            references={},
        )
    allowed = 1 if failure == "ceiling" else 0
    assert controller.attempts == allowed
    assert len(list((output / "frontend-authority").glob("*-grant.json"))) == allowed
    if failure == "ceiling":
        refusal = json.loads((output / "frontend-dispatch/00000002-reservation.json").read_text())
        assert refusal["decision"] == "refused" and refusal["reserved_after"] == 1


def test_primary_failure_still_retains_transcript_when_dispatch_reference_fails(
    tmp_path, monkeypatch
):
    """MON-12/MON-13: failed reservation retention cannot discard acquired source diagnostics."""
    from pathlib import Path

    from aisle.harness import matched_app_server as runner
    from aisle.harness.frontend_dispatch import DispatchBudget

    controller, _, output = _controller(tmp_path, "typed")
    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    original = DispatchBudget._retain

    def unavailable(authority, name, value):
        if name.endswith("-reservation.json"):
            raise OSError("primary reservation failure")
        return original(authority, name, value)

    monkeypatch.setattr(DispatchBudget, "_retain", unavailable)

    def frontend(*args, **kwargs):
        kwargs["output"].mkdir()
        (kwargs["output"] / "00000001-received.json").write_bytes(b"{}\n")
        kwargs["handle_call"](
            {"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}, b"{}\n"
        )

    monkeypatch.setattr(runner, "run_app_server", frontend)
    with pytest.raises(OSError, match="primary reservation failure"):
        runner.run_authorized_app_server(
            controller,
            ["unused"],
            cwd=tmp_path,
            env={},
            launch={"app_server": {"baseInstructions": "system", "developerInstructions": "task"}},
            budget={
                **controller.plan["arms"]["typed"]["budget"],
                "wall_ceiling_s": 10,
                "frontend_tool_ceiling": 1,
            },
            references={},
        )
    assert (output / "session.jsonl").read_bytes() == b"{}\n"
    assert (output / "frontend-live.json").is_file()
    assert controller.attempts == 0
