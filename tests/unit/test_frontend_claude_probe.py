"""MON-12/MON-13: distinguish observed denial from a broken Claude fixture."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.unit


def evidence():
    return {
        "rc": 0,
        "timed_out": False,
        "marker": "fixture",
        "hook": None,
        "requests": [
            {"fixture_key": True, "authorization_present": False, "tools": [], "results": []},
            {
                "fixture_key": True,
                "authorization_present": False,
                "tools": [{"name": "Bash"}],
                "results": [],
            },
            {
                "fixture_key": True,
                "authorization_present": False,
                "tools": [{"name": "Bash"}],
                "results": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_fixture",
                        "is_error": False,
                        "content": "(Bash completed with no output)",
                    }
                ],
            },
        ],
    }


def test_success_requires_a_matched_result_and_actual_side_effect():
    """MON-12: fixture success is observed execution, never complete coverage."""
    from frontend_claude_probe import summarize_probe

    report = summarize_probe(evidence())
    assert report["ok"] and report["tool_executed"] and not report["tool_blocked"]
    assert not report["complete_coverage"] and not report["confinement_verified"]


def test_denial_requires_matching_hook_and_no_side_effect():
    """MON-13: an explicit matched denial can be distinguished from failed startup."""
    from frontend_claude_probe import COMMAND, summarize_probe

    value = evidence()
    value["marker"] = None
    value["requests"][-1]["results"][0].update(is_error=True, content="AISLE fixture denial")
    value["hook"] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": "toolu_fixture",
        "tool_input": {"command": COMMAND, "description": "Local fixture marker"},
    }
    report = summarize_probe(value)
    assert report["ok"] and report["tool_blocked"] and not report["tool_executed"]
    value["hook"] = None
    assert not summarize_probe(value)["ok"]


@pytest.mark.parametrize(
    "failure", ["exit", "timeout", "marker", "identity", "auth", "duplicate", "error", "catalog"]
)
def test_invalid_fixture_cannot_supply_positive_evidence(failure):
    """MON-13: broken, unauthenticated or unmatched observations fail classification."""
    from frontend_claude_probe import summarize_probe

    value = copy.deepcopy(evidence())
    if failure == "exit":
        value["rc"] = 1
    elif failure == "timeout":
        value["timed_out"] = True
    elif failure == "marker":
        value["marker"] = None
    elif failure == "identity":
        value["requests"][-1]["results"][0]["tool_use_id"] = "different"
    elif failure == "auth":
        value["requests"][0]["authorization_present"] = True
    elif failure == "duplicate":
        value["requests"][-1]["results"] *= 2
    elif failure == "error":
        value["requests"][-1]["results"][0]["is_error"] = True
    elif failure == "catalog":
        value["requests"][1]["tools"] = []
    report = summarize_probe(value)
    assert not report["ok"] and report["tool_executed"] is None and report["tool_blocked"] is None


def test_preliminary_request_does_not_consume_tool_instruction():
    """MON-12: a tool-less preliminary request cannot consume the one Bash call."""
    from frontend_claude_probe import select_tool_call

    assert not select_tool_call({"tools": []}, issued=False)
    assert select_tool_call({"tools": [{"name": "Bash"}]}, issued=False)
    assert not select_tool_call({"tools": [{"name": "Bash"}]}, issued=True)
