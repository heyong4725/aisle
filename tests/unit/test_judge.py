"""Table-driven unit tests for the oracle judge (SPEC 040 VER-1, VER-2,
VER-3) — pure function, no dora, no sim (CON-12)."""

import numpy as np
import pytest

from aisle.verifier.oracle import (
    FAILURE_CLASSES,
    JudgeCfg,
    judge,
    load_thresholds,
    threshold_kwargs,
)

pytestmark = pytest.mark.unit

# physically grounded: tray base top at 0.04 m (a box resting in the tray
# has center z >= 0.08, ABOVE the dropped threshold; floor-resting centers
# are below it)
TRAY_MIN = (0.25, -0.45, 0.04)
TRAY_MAX = (0.45, -0.25, 0.19)
HALF = (0.03, 0.02, 0.05)
SHELF = [(0.50, -0.10 + 0.06 * i, 0.10) for i in range(5)]  # initial shelf poses
IN_TRAY = (0.35, -0.35, 0.09)  # box center resting inside the tray
IDENTITY = (0.0, 0.0, 0.0, 1.0)  # TC-1 (x, y, z, w)
TILT_20 = (0.1736, 0.0, 0.0, 0.9848)  # ~20 deg about x
TILT_45 = (0.3827, 0.0, 0.0, 0.9239)  # ~45 deg
TILT_90 = (0.7071, 0.0, 0.0, 0.7071)  # flat on its side


THRESHOLDS = load_thresholds()


def make_cfg(**overrides) -> JudgeCfg:
    # same thresholds->field wiring as build_judge_cfg (threshold_kwargs)
    defaults = dict(
        tray_min=TRAY_MIN,
        tray_max=TRAY_MAX,
        box_half_extents=tuple([HALF] * 5),
        initial_positions=tuple(SHELF),
        timeout_s=30.0,
        robot_home_error_rad=0.05,
        **threshold_kwargs(THRESHOLDS),
    )
    defaults.update(overrides)
    return JudgeCfg(**defaults)


def make_state(positions=None, quats=None) -> np.ndarray:
    positions = positions or list(SHELF)
    quats = quats or [IDENTITY] * 5
    blocks = []
    for pos, quat in zip(positions, quats, strict=True):
        blocks.extend([*pos, *quat])
    return np.asarray(blocks, dtype=np.float32)


def moved(positions, idx, new_pos):
    out = list(positions)
    out[idx] = new_pos
    return out


CASES = [
    # (name, state_kwargs, target_idx, t, cfg_overrides, expected)
    ("ongoing_at_start", {}, 0, 0.1, {}, ("ongoing", None)),
    ("ongoing_mid_episode", {}, 2, 15.0, {}, ("ongoing", None)),
    (
        "success_upright_in_tray",
        {"positions": moved(SHELF, 0, IN_TRAY)},
        0,
        5.0,
        {},
        ("success", None),
    ),
    (
        "success_toppled_within_band",
        {"positions": moved(SHELF, 0, IN_TRAY), "quats": moved([IDENTITY] * 5, 0, TILT_20)},
        0,
        5.0,
        {},
        ("success", None),
    ),
    (
        "success_at_exact_upright_threshold",
        {
            "positions": moved(SHELF, 0, IN_TRAY),
            "quats": moved([IDENTITY] * 5, 0, (0.2588, 0.0, 0.0, 0.9659)),
        },  # 30 deg
        0,
        5.0,
        {},
        ("success", None),
    ),
    (
        "tilt_45_in_tray_not_success",
        {"positions": moved(SHELF, 1, IN_TRAY), "quats": moved([IDENTITY] * 5, 1, TILT_45)},
        1,
        5.0,
        {},
        ("ongoing", None),
    ),
    (
        "tilt_45_in_tray_times_out",
        {"positions": moved(SHELF, 1, IN_TRAY), "quats": moved([IDENTITY] * 5, 1, TILT_45)},
        1,
        30.0,
        {},
        ("fail", "timeout"),
    ),
    (
        "flat_toppled_in_tray_times_out",
        {"positions": moved(SHELF, 0, IN_TRAY), "quats": moved([IDENTITY] * 5, 0, TILT_90)},
        0,
        31.0,
        {},
        ("fail", "timeout"),
    ),
    (
        "wrong_object_immediate",
        {"positions": moved(SHELF, 3, IN_TRAY)},
        0,
        0.05,
        {},
        ("fail", "wrong_object"),
    ),
    (
        "wrong_object_beats_target_success",
        {"positions": moved(moved(SHELF, 0, IN_TRAY), 3, (0.30, -0.30, 0.09))},
        0,
        5.0,
        {},
        ("fail", "wrong_object"),
    ),
    (
        "wrong_object_beats_timeout",
        {"positions": moved(SHELF, 4, IN_TRAY)},
        0,
        99.0,
        {},
        ("fail", "wrong_object"),
    ),
    (
        "dropped_on_floor",
        {"positions": moved(SHELF, 0, (0.5, 0.0, 0.01))},
        0,
        5.0,
        {},
        ("fail", "dropped"),
    ),
    (
        "dropped_beats_timeout",
        {"positions": moved(SHELF, 0, (0.5, 0.0, 0.005))},
        0,
        60.0,
        {},
        ("fail", "dropped"),
    ),
    (
        "collision_knocked_neighbor",
        {"positions": moved(SHELF, 2, (0.50, 0.05, 0.10))},
        0,
        5.0,
        {},
        ("fail", "collision"),
    ),
    (
        "small_jitter_is_not_collision",
        {"positions": moved(SHELF, 2, (0.50, 0.031, 0.10))},
        0,
        5.0,
        {},
        ("ongoing", None),
    ),
    (
        "timeout_after_moving",
        {"positions": moved(SHELF, 0, (0.45, -0.05, 0.10))},
        0,
        30.0,
        {},
        ("fail", "timeout"),
    ),
    ("never_grasped_unmoved", {}, 0, 30.0, {}, ("fail", "never_grasped")),
    (
        "never_grasped_within_move_epsilon",
        {"positions": moved(SHELF, 0, (0.505, -0.10, 0.10))},
        0,
        30.0,
        {},
        ("fail", "never_grasped"),
    ),
    (
        "home_error_blocks_success",
        {"positions": moved(SHELF, 0, IN_TRAY)},
        0,
        5.0,
        {"robot_home_error_rad": 0.5},
        ("ongoing", None),
    ),
    (
        "home_unreported_blocks_success",
        {"positions": moved(SHELF, 0, IN_TRAY)},
        0,
        5.0,
        {"robot_home_error_rad": None},
        ("ongoing", None),
    ),
    (
        "home_check_disabled_allows_success",
        {"positions": moved(SHELF, 0, IN_TRAY)},
        0,
        5.0,
        {"robot_home_error_rad": None, "home_check_enabled": False},
        ("success", None),
    ),
    (
        "aabb_straddling_tray_edge_not_success",
        {"positions": moved(SHELF, 0, (0.446, -0.35, 0.09))},
        0,
        5.0,
        {},
        ("ongoing", None),
    ),
    (
        "aabb_within_margin_is_success",
        {"positions": moved(SHELF, 0, (0.424, -0.35, 0.09))},
        0,
        5.0,
        {},
        ("success", None),
    ),
    (
        "sideways_quat_is_high_tilt",
        {"positions": moved(SHELF, 0, IN_TRAY), "quats": moved([IDENTITY] * 5, 0, TILT_90)},
        0,
        5.0,
        {},
        ("ongoing", None),
    ),
]


@pytest.mark.parametrize(
    "name,state_kwargs,target_idx,t,cfg_overrides,expected", CASES, ids=[c[0] for c in CASES]
)
def test_judge_table(name, state_kwargs, target_idx, t, cfg_overrides, expected):
    """VER-1, VER-2, VER-3: the pure judge maps every oracle situation to
    its exact verdict — the five-class failure taxonomy with wrong_object's
    immediacy, the toppled-but-inside success rule, the upright band, the
    AABB-in-tray criterion with margin, and the robot-home gate."""
    verdict = judge(make_state(**state_kwargs), target_idx, t, make_cfg(**cfg_overrides))
    assert verdict == expected


def test_failure_classes_are_exactly_ver3():
    """VER-3: the taxonomy is exactly these five classes."""
    assert FAILURE_CLASSES == ("wrong_object", "dropped", "timeout", "never_grasped", "collision")


def test_thresholds_come_from_toml():
    """VER-2: every judge threshold is loaded from thresholds.toml."""
    thresholds = load_thresholds()
    assert thresholds["success"]["upright_max_deg"] == 30.0
    assert set(thresholds["failure"]) == {
        "dropped_z_m",
        "move_epsilon_m",
        "knock_epsilon_m",
        "wrong_object_entry_height_m",
    }


def test_late_success_is_timeout():
    """VER-2/TC-8 (cross-review): a target placed correctly AFTER the
    deadline is a timeout, never a late success — metrics must not
    inflate."""
    state = make_state(positions=moved(SHELF, 0, IN_TRAY))
    assert judge(state, 0, 31.0, make_cfg()) == ("fail", "timeout")


def test_yawed_box_footprint_is_rotation_aware():
    """VER-2 (cross-review): tray containment uses the WORLD AABB — a
    45-degree-yawed box near the tray edge overhangs where the unrotated
    footprint would not."""
    yaw_45 = (0.0, 0.0, 0.3827, 0.9239)
    near_edge = (0.42, -0.35, 0.09)
    state = make_state(positions=moved(SHELF, 0, near_edge), quats=moved([IDENTITY] * 5, 0, yaw_45))
    assert judge(state, 0, 5.0, make_cfg()) == ("ongoing", None)  # overhangs when yawed
    upright_state = make_state(positions=moved(SHELF, 0, near_edge))
    assert judge(upright_state, 0, 5.0, make_cfg()) == ("success", None)


def test_box_above_tray_footprint_is_not_wrong_object():
    """VER-3 (review): a non-target box standing OVER the tray footprint at
    shelf height (or arcing above it) has not "entered the tray" — the
    entry region is bounded in z."""
    hovering = (0.35, -0.35, 0.50)
    state = make_state(positions=moved(SHELF, 3, hovering))
    verdict = judge(state, 0, 5.0, make_cfg())
    assert verdict != ("fail", "wrong_object")
    # a non-target box displaced this far from its start IS a knocked box
    assert verdict == ("fail", "collision")


def test_floor_resting_box_is_dropped():
    """VER-3 (review): a box resting on the floor (center ~0.05 m for a
    tall face) IS classified dropped — the threshold must be reachable by
    physically attainable poses."""
    state = make_state(positions=moved(SHELF, 0, (0.6, 0.1, 0.05)))
    assert judge(state, 0, 5.0, make_cfg()) == ("fail", "dropped")


def test_airborne_target_above_tray_is_not_success():
    """VER-2 (PR review): success means RESTING in the tray — an upright
    target carried or thrown over the tray footprint, robot home, must not
    score while airborne."""
    airborne = (0.35, -0.35, 0.30)
    state = make_state(positions=moved(SHELF, 0, airborne))
    assert judge(state, 0, 5.0, make_cfg()) == ("ongoing", None)
