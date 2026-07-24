"""Unit tests for the S1 expert's pure planning/geometry (T15, ADR-18).
Real IK on realistic store targets — no dora, no sim (CON-12)."""

import math

import numpy as np
import pytest
from cli_helpers import run_cli

pytestmark = pytest.mark.unit


def _plano():
    from aisle.scenes.store import load_planogram

    return load_planogram()


def _home():
    from aisle.scenes.pharmacy import load_physics

    return np.asarray(load_physics()["embodiment"]["mobile"]["home_qpos"], dtype=np.float32)


def test_park_pose_faces_the_slot():
    """ADR-18: the park pose stands PARK_STANDOFF_M in front of the slot
    along the unit facing, yawed to face the unit — so the slot lands
    dead-ahead in the base frame at desk-like geometry."""
    from aisle.nodes.s1_expert import PARK_STANDOFF_M, park_pose_for_slot, to_base_frame
    from aisle.scenes.store import slot_world_pose

    plano = _plano()
    for slot_id in ("A1-L0-S1", "B1-L1-S0", "A2-L1-S2"):
        world, _ = slot_world_pose(plano, slot_id)
        park = park_pose_for_slot(plano, slot_id)
        slot_base = to_base_frame(world, park)
        assert slot_base[0] == pytest.approx(PARK_STANDOFF_M, abs=1e-6), slot_id
        assert slot_base[1] == pytest.approx(0.0, abs=1e-6), slot_id


def test_to_base_frame_round_trip():
    from aisle.nodes.s1_expert import to_base_frame

    base = [1.5, -1.0, -math.pi / 2]
    p = to_base_frame([1.72, -1.55, 0.4], base)
    # facing -y: a point 0.55 ahead, 0.22 to the left
    assert p[0] == pytest.approx(0.55, abs=1e-6)
    assert p[1] == pytest.approx(0.22, abs=1e-6)
    assert p[2] == pytest.approx(0.4, abs=1e-6)


def test_pick_and_place_stages_solve_for_an_l1_slot():
    """ADR-18: the split pick (rise..carry, grip held) and place
    (transfer..home) stage lists SOLVE with real IK for a realistic L1
    pick at the park geometry and a counter drop — the seed-1 acceptance
    episode's actual poses."""
    from aisle.nodes.s1_expert import pick_stages, place_stages
    from aisle.scenes.pharmacy import load_meds

    meds = load_meds()
    home = _home()
    plano = _plano()
    counter_top = plano["store"]["counter_pos"][2] + plano["store"]["counter_size"][2] / 2
    size = meds["omeprazole"]["size"]
    item_pos_base = [0.55, 0.0, 0.36 + size[2] / 2]
    stages, q_carry, place_z, err = pick_stages(item_pos_base, math.pi, size, home, counter_top)
    assert err is None, err
    assert [s.name for s in stages] == [
        "rise",
        "staging",
        "pregrasp",
        "advance",
        "close",
        "lift",
        "retract",
        "carry",
    ]
    # the grip closes at `close` and stays held through the carry
    assert stages[4].gripper == 1.0 and stages[-1].gripper == 1.0
    assert q_carry is not None
    assert place_z > counter_top  # the drop TCP hovers above the counter top

    placed, err = place_stages(q_carry, (0.42, 0.0), place_z, home)
    assert err is None, err
    assert [s.name for s in placed] == ["unwind", "transfer", "lower", "release", "clear", "home"]
    assert placed[2].gripper == 1.0 and placed[3].gripper == 0.0  # open only when lowered


def test_pick_solves_across_the_nav_tolerance_envelope():
    """T15 live-run regression: the widest pose nav can ACCEPT — the
    CAPTURE band (a stalled drive hands off to rotate inside it, PR #21
    round 3) times the arrival yaw — offsets the item in the base frame,
    and the pick chain must SOLVE at those corners, not just the nominal
    park (the first live episode failed IK exactly here)."""
    from aisle.mobility.nav import load_nav_params
    from aisle.nodes.s1_expert import PARK_STANDOFF_M, pick_stages, place_stages
    from aisle.scenes.pharmacy import load_meds

    params = load_nav_params("mobile")
    tol, ytol = params["capture_tol_m"], params["arrival_yaw_rad"]
    meds = load_meds()
    home = _home()
    counter_top = 0.55
    for category in ("amoxicillin", "omeprazole"):
        size = meds[category]["size"]
        z = 0.36 + size[2] / 2
        for dx in (-tol, 0.0, tol):
            for dy in (-tol, 0.0, tol):
                for dyaw in (-ytol, 0.0, ytol):
                    # base offset (dx, dy, dyaw) => item moves oppositely
                    import math as _m

                    rel_x = PARK_STANDOFF_M - dx
                    rel_y = -dy
                    cos_y, sin_y = _m.cos(-dyaw), _m.sin(-dyaw)
                    pos = [rel_x * cos_y - rel_y * sin_y, rel_x * sin_y + rel_y * cos_y, z]
                    yaw = _m.pi - dyaw
                    stages, q_carry, place_z, err = pick_stages(pos, yaw, size, home, counter_top)
                    assert err is None, (category, dx, dy, dyaw, err)
                    placed, perr = place_stages(q_carry, (0.42, 0.0), place_z, home)
                    assert perr is None, (category, dx, dy, dyaw, perr)


def test_task_planner_prefers_open_sky_slots():
    """ADR-18: S1 sources come from the HIGHEST level first (open sky for
    the top-down grasp) — the seed-1 order sources are both L1."""
    from aisle.nodes.task_planner import plan_subtasks
    from aisle.scenes.store import generate_episode

    plano = _plano()
    plan = plan_subtasks(generate_episode(1, "S1"), plano)
    picks = [s["slot"] for s in plan if s["op"] == "pick"]
    assert picks and all("-L1-" in slot for slot in picks), picks


def test_module_import_stays_sim_and_dora_free():
    probe = (
        "import sys; import aisle.nodes.s1_expert; "
        "assert 'genesis' not in sys.modules and 'dora' not in sys.modules"
    )
    proc = run_cli(["-c", probe])
    assert proc.returncode == 0, proc.stderr
