"""SPEC 210 MOB-4 acceptance (named by the spec) + the MOB-3 motion-sink
gate for base_cmd. No dora, no sim (CON-12): validator logic only."""

import pytest
import yaml
from cli_helpers import run_json

pytestmark = pytest.mark.unit


def _validate(graph, embodiment="mobile"):
    return run_json("aisle.harness.cli", "validate", str(graph), "--embodiment", embodiment)


def _codes(report: dict) -> set[str]:
    return {e["code"] for e in report.get("errors", [])}


def _write(tmp, nodes) -> str:
    path = tmp / "g.yaml"
    path.write_text(yaml.safe_dump({"nodes": nodes}))
    return path


def test_franka_arm_validates_under_mobile():
    """MOB-4: mobile resolves to the franka arm, so a franka-arm node
    validates unchanged under the mobile profile."""
    from aisle.harness.validate import validate_nodes

    manifests = {"ik-trajectory": {"embodiment": {"arm": ["franka"], "gripper": "parallel"}}}
    errors, _ = validate_nodes(
        [{"id": "ik-trajectory"}], manifests, set(), "mobile", allow_unproven=True
    )
    assert not [e for e in errors if e["code"] == "EMBODIMENT_MISMATCH"]


def test_base_requiring_node_mismatches_on_fixed_base():
    """MOB-4: a base-requiring node validates under mobile but is an
    EMBODIMENT_MISMATCH on a fixed-base (franka) graph."""
    from aisle.harness.validate import validate_nodes

    manifests = {"nav-action": {"embodiment": {"arm": ["franka"], "base": ["mobile"]}}}
    ok, _ = validate_nodes([{"id": "nav-action"}], manifests, set(), "mobile", allow_unproven=True)
    assert not [e for e in ok if e["code"] == "EMBODIMENT_MISMATCH"]
    bad, _ = validate_nodes([{"id": "nav-action"}], manifests, set(), "franka", allow_unproven=True)
    assert [e for e in bad if e["code"] == "EMBODIMENT_MISMATCH"]


def test_unguarded_base_cmd_is_rejected(tmp_path):
    """MOB-3 (PR #14 review): base_cmd is a motion sink — a base command
    reaching the bridge without traversing the budget guard is MOTION_UNGATED."""
    graph = _write(
        tmp_path,
        [
            {"id": "nav-action", "outputs": ["base_cmd"]},
            {
                "id": "dora-genesis",
                "inputs": {"base_cmd": "nav-action/base_cmd"},
                "outputs": ["base_pose"],
            },
        ],
    )
    rc, report = _validate(graph)
    assert "MOTION_UNGATED" in _codes(report)
    assert rc != 0


def test_guarded_base_cmd_passes_the_motion_gate(tmp_path):
    """MOB-3: routed through the budget guard, base_cmd is NOT MOTION_UNGATED."""
    graph = _write(
        tmp_path,
        [
            {"id": "nav-action", "outputs": ["base_cmd"]},
            {
                "id": "budget-guard",
                "inputs": {"base_cmd": "nav-action/base_cmd"},
                "outputs": ["base_cmd_safe"],
            },
            {
                "id": "dora-genesis",
                "inputs": {"base_cmd": "budget-guard/base_cmd_safe"},
                "outputs": ["base_pose"],
            },
        ],
    )
    _, report = _validate(graph)
    assert "MOTION_UNGATED" not in _codes(report)
