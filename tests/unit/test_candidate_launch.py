"""Arm launch arguments derived from the admitted candidate, not literals.

MON-3: the launcher binds the candidate's graph/module; MON-4: both arms receive
the same verifier, reset, embodiment, seeds and budgets; CON-5: pure and
deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aisle.harness import candidates as cand
from aisle.harness.candidate_launch import arm_arguments

pytestmark = pytest.mark.unit

VIEW = Path("/view")
BUDGET = {"seeds": [3, 5], "timeout_s": 30}


def _fields(**overrides):
    fields = cand.launch_fields({"schema_version": cand.DEVELOPMENT_SCHEMA_V1})
    return {**fields, **overrides}


def test_run_arguments_bind_candidate_paths_and_shared_run_parameters():
    """MON-3/MON-4: typed and monolithic run commands share every run parameter."""
    fields = _fields(
        typed_graph="graphs/expert_t1_l2.yaml",
        monolithic_module="experts/monolithic/expert_t1_l2.py",
        monolithic_template="graphs/monolithic_t1_l2.yaml",
        verifier="realistic",
    )
    typed = arm_arguments("typed", "run", fields, VIEW, BUDGET, root=Path("/root"), run_id="r1")
    mono = arm_arguments("monolithic", "run", fields, VIEW, BUDGET, root=Path("/root"), run_id="r1")
    assert typed[:3] == ["rollout", "--graph", "/view/graphs/expert_t1_l2.yaml"]
    assert mono[:4] == ["monolith", "run", "--module", "/view/experts/monolithic/expert_t1_l2.py"]
    for args in (typed, mono):
        assert args[args.index("--verifier") + 1] == "realistic"
        assert args[args.index("--reset") + 1] == "teleport"
        assert args[args.index("--embodiment") + 1] == "franka"
        assert args[args.index("--seeds") + 1] == "3,5"
        assert args[args.index("--episodes") + 1] == "2"
        assert args[args.index("--run-id") + 1] == "r1"
        assert args[args.index("--timeout-s") + 1] == "30"
    assert mono[mono.index("--template") + 1] == "graphs/monolithic_t1_l2.yaml"


def test_check_arguments_bind_candidate_paths_and_embodiment():
    """MON-3: check commands validate the candidate's own artifacts."""
    fields = _fields()
    typed = arm_arguments("typed", "check", fields, VIEW, BUDGET)
    mono = arm_arguments("monolithic", "check", fields, VIEW, BUDGET)
    assert typed == [
        "validate",
        "/view/graphs/expert_t1.yaml",
        "--root",
        "/view",
        "--embodiment",
        "franka",
    ]
    assert mono == [
        "monolith",
        "check",
        "--module",
        "/view/experts/monolithic/expert_t1.py",
        "--embodiment",
        "franka",
    ]


def test_unknown_arm_or_operation_refuses():
    """CON-5: no silent default for an unresolved arm or operation."""
    with pytest.raises(ValueError):
        arm_arguments("hybrid", "run", _fields(), VIEW, BUDGET, root=Path("/r"), run_id="x")
    with pytest.raises(ValueError):
        arm_arguments("typed", "deploy", _fields(), VIEW, BUDGET)
