"""Unit tests for the T14 retail capability extension (design doc §11.4,
SPEC 200 RS-10, ADR-17): the eight manifests and their oracle-rung stub
pure cores. No dora, no sim (CON-12); graph-level exercise arrives with
T15's S1 expert graph."""

import math

import numpy as np
import pytest
import yaml
from cli_helpers import REPO_ROOT, run_cli

pytestmark = pytest.mark.unit

MANIFESTS = REPO_ROOT / "registry" / "manifests"

NEW_CAPABILITIES = {
    "base-driver-sim": "base_actuation",
    "waypoint-nav": "waypoint_navigation",
    "patrol-planner": "patrol_planning",
    "order-reader": "order_reading",
    "stock-detector": "stock_detection",
    "misplacement-detector": "misplacement_detection",
    "placement-controller": "fine_placement",
    "task-planner": "task_planning",
}


def _plano():
    from aisle.scenes.store import load_planogram

    return load_planogram()


def _state_at_home(cfg, overrides: dict | None = None) -> np.ndarray:
    """oracle_state with every item at its spawn pose (TC-1 quats)."""
    blocks = []
    for idx, item_id in enumerate(cfg.item_ids):
        x, y, z, yaw = (overrides or {}).get(item_id, cfg.home_poses[idx])
        half = yaw / 2
        blocks.extend([x, y, z, 0.0, 0.0, math.sin(half), math.cos(half)])
    return np.asarray(blocks, dtype=np.float32)


def test_manifests_exist_with_expected_capabilities():
    """§11.4: every named capability has a manifest providing it, each
    source file exists, and none provides a rearrangement skill (CAP-5's
    deliberate gap holds)."""
    for manifest_id, provides in sorted(NEW_CAPABILITIES.items()):
        manifest = yaml.safe_load((MANIFESTS / f"{manifest_id}.yaml").read_text())
        assert provides in manifest["provides"], manifest_id
        assert (REPO_ROOT / manifest["source"]).is_file(), manifest["source"]
        assert not any("rearrang" in p for p in manifest["provides"])


def test_base_driver_sim_has_a_motion_evalcard():
    """CAP-6: base-driver-sim is motion-class, so eval MUST NOT be null —
    it carries the T11 mobile conformance evalcard."""
    manifest = yaml.safe_load((MANIFESTS / "base-driver-sim.yaml").read_text())
    assert manifest["safety_class"] == "motion"
    assert manifest["eval"] is not None
    assert "test_contract_mobile" in manifest["eval"]["suite"]


def test_order_reader_oracle_rung():
    """RS-10: the oracle rung republishes the goal's order verbatim; a
    goal without an order is a loud wiring error."""
    from aisle.nodes.order_reader import read_order
    from aisle.scenes.store import generate_episode

    goal = generate_episode(3, "S1")
    order = read_order(goal)
    assert order["order"] == goal["order"] and order["seed"] == 3
    with pytest.raises(ValueError, match="no order"):
        read_order(generate_episode(3, "S2"))


def test_stock_detector_finds_exactly_the_destocked_slots():
    """RS-10: on the S2 initial state, the empty slots are exactly the
    episode's de-stocked ones, with their planogram categories."""
    from aisle.nodes.stock_detector import detect_stock
    from aisle.scenes.store import generate_episode
    from aisle.verifier.retail import build_retail_cfg

    plano = _plano()
    goal = generate_episode(4, "S2")
    cfg = build_retail_cfg(plano, goal)
    report = detect_stock(_state_at_home(cfg), plano, cfg)
    expected = {entry["slot"]: entry["category"] for entry in goal["restock"]}
    assert {e["slot"]: e["category"] for e in report["empty_slots"]} == expected
    # deterministic (CON-5)
    assert report == detect_stock(_state_at_home(cfg), plano, cfg)


def test_misplacement_detector_finds_exactly_the_swap():
    """RS-10: on the S3 initial state, the misplaced items are exactly the
    episode's two swapped items with found_in/belongs_in; a clean S1 state
    reports none."""
    from aisle.nodes.misplacement_detector import detect_misplacements
    from aisle.scenes.store import generate_episode
    from aisle.verifier.retail import build_retail_cfg

    plano = _plano()
    goal = generate_episode(4, "S3")
    cfg = build_retail_cfg(plano, goal)
    report = detect_misplacements(_state_at_home(cfg), plano, cfg)
    expected = {(e["item"], e["found_in"], e["belongs_in"]) for e in goal["misplaced"]}
    assert {(e["item"], e["found_in"], e["belongs_in"]) for e in report["misplaced"]} == expected

    clean_goal = generate_episode(4, "S1")
    clean_cfg = build_retail_cfg(plano, clean_goal)
    assert detect_misplacements(_state_at_home(clean_cfg), plano, clean_cfg) == {"misplaced": []}


def test_patrol_sequence_covers_every_zone():
    """§11.4: the patrol covers every shelf zone exactly once, in a
    deterministic order, using the ADR-15 named locations."""
    from aisle.mobility.nav import load_locations
    from aisle.nodes.patrol_planner import patrol_sequence

    plano = _plano()
    goals = patrol_sequence(plano)
    zones = {u["zone"] for u in plano["units"].values()}
    assert len(goals) == len(zones)
    locations = load_locations()
    for goal in goals:
        assert goal["location"] in locations
    assert goals == patrol_sequence(plano)


def test_placement_target_is_the_rs4_pass_point():
    """§11.4: the placement target is the pose where every RS-4 criterion
    passes — judged by the retail verifier itself."""
    from aisle.nodes.placement_controller import placement_target
    from aisle.scenes.pharmacy import load_meds
    from aisle.verifier.retail import build_retail_cfg, placement_check

    plano = _plano()
    meds = load_meds()
    target = placement_target(plano, "A1-L0-S0", "amoxicillin", meds)
    assert len(target) == 7
    cfg = build_retail_cfg(plano, {"scenario": "S1", "seed": 0})
    x, y, z, qx, qy, qz, qw = target
    yaw = math.atan2(2 * qw * qz, 1 - 2 * qz * qz)
    half = tuple(s / 2 for s in meds["amoxicillin"]["size"])
    score = placement_check(np.array([x, y, z]), yaw, half, "A1-L0-S0", plano, cfg)
    assert all(score[c] for c in ("pos", "yaw", "front_face", "overhang", "alignment"))
    with pytest.raises(ValueError, match="unknown slot"):
        placement_target(plano, "Z9-L9-S9", "amoxicillin", meds)


def test_task_planner_covers_every_goal_parameter():
    """§11.4: the subtask sequence covers each order line qty times (S1),
    each restock entry (S2), and each misplaced item (S3), deterministic
    per goal; an empty goal is a loud error."""
    from aisle.nodes.task_planner import plan_subtasks
    from aisle.scenes.store import generate_episode

    plano = _plano()
    s1 = generate_episode(5, "S1")
    plan = plan_subtasks(s1, plano)
    picks = [s for s in plan if s["op"] == "pick"]
    assert len(picks) == sum(line["qty"] for line in s1["order"])
    assert plan == plan_subtasks(s1, plano)

    s2 = generate_episode(5, "S2")
    plan2 = plan_subtasks(s2, plano)
    assert [s["slot"] for s in plan2 if s["op"] == "place"] == [e["slot"] for e in s2["restock"]]

    s3 = generate_episode(5, "S3")
    plan3 = plan_subtasks(s3, plano)
    assert [s["item"] for s in plan3 if s["op"] == "pick"] == [e["item"] for e in s3["misplaced"]]
    with pytest.raises(ValueError, match="no plannable"):
        plan_subtasks({"scenario": "S1", "seed": 0}, plano)


@pytest.mark.parametrize(
    "module",
    [
        "order_reader",
        "stock_detector",
        "misplacement_detector",
        "patrol_planner",
        "placement_controller",
        "task_planner",
    ],
)
def test_stub_imports_stay_sim_and_dora_free(module):
    """CON-12: importing a stub's pure core must pull in neither genesis
    nor dora."""
    probe = (
        f"import sys; import aisle.nodes.{module}; "
        "assert 'genesis' not in sys.modules, 'genesis at import'; "
        "assert 'dora' not in sys.modules, 'dora at import'"
    )
    proc = run_cli(["-c", probe])
    assert proc.returncode == 0, proc.stderr
