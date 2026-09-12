"""MON-13: replay cases attempt a changed effect while dispatch quota remains."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ceiling", [2, 4])
def test_replay_generator_reuses_identity_with_changed_effect(tmp_path, ceiling):
    """MON-13: identity replay, independently of exhausted quota, prevents the append."""
    from test_matched_conformance_session import _provider_replay_frame

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
    from aisle.harness.frontend_effects import command_probe

    target, marker = "task.yaml", "# AISLE probe replay"
    calls = []
    for index in (2, 3):
        raw = _provider_replay_frame(index, target=target, marker=marker)
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: ")]
        item = next(row["item"] for row in events if row["type"] == "response.output_item.done")
        assert next(
            row["response"]["output"] for row in events if row["type"] == "response.completed"
        ) == [item]
        calls.append((item, raw))
    assert calls[0][0]["call_id"] == calls[1][0]["call_id"] == "call_2"
    assert json.loads(calls[0][0]["arguments"])["cmd"] == ":"
    assert json.loads(calls[1][0]["arguments"])["cmd"] == command_probe(target, marker)
    delivered = []
    with DispatchBudget(tmp_path / "dispatch", session_id="replay", ceiling=ceiling) as budget:
        for index, (item, raw) in enumerate(calls):
            call = {"turn_id": "turn", "call_id": item["call_id"], "tool_name": item["name"]}
            if index == 0:
                budget.dispatch(call, raw, lambda _: delivered.append("noop"))
            else:
                with pytest.raises(DispatchRefused, match="replay"):
                    budget.dispatch(call, raw, lambda _: delivered.append("effect"))
    assert delivered == ["noop"]
