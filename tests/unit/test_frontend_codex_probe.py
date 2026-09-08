"""MON-12/MON-13: a missing side effect alone cannot prove frontend denial."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.unit


def evidence(*, blocked=False):
    command = "printf fixture > marker.txt"
    return {
        "rc": 0,
        "timed_out": False,
        "marker": None if blocked else "fixture",
        "requests": [
            {"authorization_present": False, "tool_outputs": []},
            {
                "authorization_present": False,
                "tool_outputs": [
                    {
                        "type": "function_call_output",
                        "call_id": "call_fixture",
                        "output": (
                            (
                                "Command blocked by PreToolUse hook: AISLE fixture denial. "
                                f"Command: {command}"
                            )
                            if blocked
                            else "Process exited with code 0\nOutput:\n"
                        ),
                    }
                ],
            },
        ],
        "hook": {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "call_fixture",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
    }


@pytest.mark.parametrize("blocked", [False, True])
def test_probe_distinguishes_observed_execution_from_denial_without_coverage_claim(blocked):
    """MON-12: successful probing does not attest the whole frontend or confinement."""
    from frontend_codex_probe import summarize_probe

    result = summarize_probe(evidence(blocked=blocked))
    assert result["ok"] is True, result
    assert result["tool_executed"] is (not blocked)
    assert result["tool_blocked"] is blocked
    assert result["complete_coverage"] is False
    assert result["confinement_verified"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "process_failed",
        "timeout",
        "missing_reply",
        "wrong_call",
        "duplicate_reply",
        "auth",
        "no_effect",
        "wrong_effect",
    ],
)
def test_incomplete_or_mismatched_probe_is_not_denial_evidence(mutation):
    """MON-13: launch failure and inconsistent receipts cannot masquerade as prevention."""
    from frontend_codex_probe import summarize_probe

    row = evidence()
    if mutation == "process_failed":
        row["rc"] = 1
    elif mutation == "timeout":
        row["timed_out"] = True
    elif mutation == "missing_reply":
        row["requests"].pop()
    elif mutation == "wrong_call":
        row["requests"][1]["tool_outputs"][0]["call_id"] = "another"
    elif mutation == "duplicate_reply":
        row["requests"][1]["tool_outputs"] *= 2
    elif mutation == "auth":
        row["requests"][0]["authorization_present"] = True
    elif mutation == "no_effect":
        row["marker"] = None
    elif mutation == "wrong_effect":
        row["marker"] = "other"
    result = summarize_probe(row)
    assert result["ok"] is False
    assert result["tool_blocked"] is None
    assert result["tool_executed"] is None
    assert result["errors"]


@pytest.mark.parametrize("mutation", ["side_effect", "wrong_hook_id", "wrong_hook_command"])
def test_denial_claim_requires_consistent_side_effect_and_hook_identity(mutation):
    """MON-12/MON-13: a denial string cannot hide execution or mismatched hook identity."""
    from frontend_codex_probe import summarize_probe

    row = copy.deepcopy(evidence(blocked=True))
    if mutation == "side_effect":
        row["marker"] = "fixture"
    elif mutation == "wrong_hook_id":
        row["hook"]["tool_use_id"] = "another"
    else:
        row["hook"]["tool_input"]["command"] = "another command"
    assert summarize_probe(row)["ok"] is False
