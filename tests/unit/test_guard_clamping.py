"""Table-driven unit tests for the budget guard's pure clamping core
(SPEC 080 BG-1, BG-2, BG-3) — no dora, no sim (CON-12)."""

from dataclasses import replace

import numpy as np
import pytest

from aisle.nodes.budget_guard import (
    clamp_gripper_cmd,
    clamp_joint_cmd,
    episode_elapsed,
    fk_ee_pos,
    load_limits,
    violation_payload,
)

pytestmark = pytest.mark.unit

LIMITS = load_limits("franka")
HOME = np.asarray(LIMITS.fallback_qpos, dtype=np.float32)


def relaxed(**overrides):
    """LIMITS with selected constraints loosened, to isolate one check."""
    return replace(LIMITS, **overrides)


NO_VEL = relaxed(qdot_max=tuple([1e9] * 9))
# shoulder pitched fully forward, elbow bent, wrist folded under: the flange
# lands ~20 cm below the floor plane (also cycled by the adversarial driver
# fixture; precondition asserted in the tests that use it)
DIVING = np.array([0.0, 1.7628, 0.0, -1.0, 0.0, 1.0, 0.785, 0.04, 0.04], dtype=np.float32)


def step(base, idx, value):
    out = np.array(base, dtype=np.float32)
    out[idx] = value
    return out


def test_in_range_command_passes_through_unchanged():
    """BG-3: a legal command is forwarded verbatim — clamping only ever
    engages on violation."""
    cmd = step(HOME, 0, HOME[0] + 0.01)
    safe, violations = clamp_joint_cmd(cmd, HOME, LIMITS, timed_out=False)
    assert violations == []
    assert np.array_equal(safe, cmd)


@pytest.mark.parametrize(
    "joint,requested,expected",
    [
        # joint 7 spins the flange about its own axis (EE position fixed),
        # so the position clamp is observable in isolation from workspace
        (6, 3.5, 2.8973),  # above q_max
        (6, -3.5, -2.8973),  # below q_min
        (7, 0.09, 0.04),  # finger beyond mechanical range
    ],
)
def test_position_limit_clamps_to_nearest_legal(joint, requested, expected):
    """BG-2/BG-3: per-joint position limits from env/limits.toml; the
    violating joint is clamped to the nearest bound, others untouched."""
    cmd = step(HOME, joint, requested)
    safe, violations = clamp_joint_cmd(cmd, HOME, NO_VEL, timed_out=False)
    assert safe[joint] == pytest.approx(expected, abs=1e-6)
    assert [v["reason"] for v in violations] == ["position"]
    assert violations[0]["joint"] == joint
    assert violations[0]["requested"] == pytest.approx(requested, abs=1e-6)
    assert violations[0]["clamped"] == pytest.approx(expected, abs=1e-6)
    others = [i for i in range(9) if i != joint]
    assert np.array_equal(safe[others], cmd[others])


@pytest.mark.parametrize("direction", [1.0, -1.0])
def test_velocity_limit_clamps_step_against_last_safe(direction):
    """BG-2: per-joint max velocity is computed against the last SAFE
    command + the contract dt; the step is clamped to +/- qdot_max*dt."""
    max_step = LIMITS.qdot_max[0] * LIMITS.cmd_dt_s
    cmd = step(HOME, 0, HOME[0] + direction * 10 * max_step)
    safe, violations = clamp_joint_cmd(cmd, HOME, LIMITS, timed_out=False)
    assert safe[0] == pytest.approx(HOME[0] + direction * max_step, abs=1e-6)
    assert [v["reason"] for v in violations] == ["velocity"]
    assert violations[0]["joint"] == 0


def test_workspace_violation_is_clamped_back_inside():
    """BG-2/BG-3: EE workspace AABB via forward kinematics on the COMMANDED
    pose; a command whose FK lands outside is pulled back along the segment
    from the last safe command until FK is inside."""
    assert fk_ee_pos(DIVING[:7])[2] < LIMITS.workspace_min[2]
    safe, violations = clamp_joint_cmd(DIVING, HOME, NO_VEL, timed_out=False)
    ee = fk_ee_pos(safe[:7])
    for axis in range(3):
        assert LIMITS.workspace_min[axis] - 1e-6 <= ee[axis] <= LIMITS.workspace_max[axis] + 1e-6
    assert "workspace" in [v["reason"] for v in violations]


def test_wall_timeout_holds_at_last_safe():
    """BG-2/BG-3: on episode wall timeout the guard clamps every command to
    the last safe one (the robot holds) — never drops the stream."""
    cmd = step(HOME, 0, HOME[0] + 0.01)
    safe, violations = clamp_joint_cmd(cmd, HOME, LIMITS, timed_out=True)
    assert np.array_equal(safe, HOME)
    assert [v["reason"] for v in violations] == ["wall_timeout"]


def test_malformed_length_holds_at_last_safe():
    """BG-3: the guard MUST NOT crash the dataflow — a command with the
    wrong dof count is replaced by the last safe command, loudly."""
    safe, violations = clamp_joint_cmd(np.zeros(4, dtype=np.float32), HOME, LIMITS, timed_out=False)
    assert np.array_equal(safe, HOME)
    assert [v["reason"] for v in violations] == ["malformed"]


def test_nan_joint_is_replaced_not_propagated():
    """BG-3: NaN/inf never reaches the robot — the offending joint is held
    at its last safe value."""
    cmd = step(HOME, 2, float("nan"))
    safe, violations = clamp_joint_cmd(cmd, HOME, LIMITS, timed_out=False)
    assert np.isfinite(safe).all()
    assert safe[2] == pytest.approx(float(HOME[2]))
    assert "malformed" in [v["reason"] for v in violations]


def test_fuzzed_commands_never_crash_and_always_legal():
    """BG-3 (MUST NOT crash): seeded fuzz (CON-5) — whatever arrives, the
    guard returns a finite command inside position limits."""
    rng = np.random.default_rng(0)
    last = HOME
    for _ in range(200):
        n = int(rng.integers(0, 12))
        cmd = (rng.standard_normal(n) * 100).astype(np.float32)
        if n and rng.random() < 0.3:
            cmd[rng.integers(0, n)] = [np.nan, np.inf, -np.inf][int(rng.integers(0, 3))]
        safe, _ = clamp_joint_cmd(cmd, last, LIMITS, timed_out=False)
        assert safe.shape == (9,) and np.isfinite(safe).all()
        assert (safe >= np.asarray(LIMITS.q_min) - 1e-6).all()
        assert (safe <= np.asarray(LIMITS.q_max) + 1e-6).all()
        last = safe


@pytest.mark.parametrize(
    "requested,expected,n_violations",
    [(0.5, 0.5, 0), (1.5, 1.0, 1), (-0.2, 0.0, 1), (float("nan"), 0.0, 1)],
)
def test_gripper_clamp(requested, expected, n_violations):
    """BG-1/BG-3: gripper_cmd is clamped to its scalar range; NaN falls to
    the open position."""
    safe, violations = clamp_gripper_cmd(float(requested), LIMITS)
    assert safe == pytest.approx(expected)
    assert len(violations) == n_violations


def test_violation_payload_shape():
    """BG-3: the violation JSON is exactly {reason, joint|axis, requested,
    clamped, seq}."""
    payload = violation_payload(
        {"reason": "position", "joint": 3, "requested": 9.0, "clamped": 1.0}, seq=7
    )
    assert payload == {"reason": "position", "joint": 3, "requested": 9.0, "clamped": 1.0, "seq": 7}
    payload = violation_payload(
        {"reason": "workspace", "axis": "z", "requested": -0.2, "clamped": 0.01}, seq=8
    )
    assert set(payload) == {"reason", "axis", "requested", "clamped", "seq"}


def test_fk_home_is_inside_workspace_and_reach():
    """BG-2 sanity: FK places the home flange inside the workspace AABB and
    within the arm's reach sphere; FK responds to joint motion."""
    ee = fk_ee_pos(HOME[:7])
    for axis in range(3):
        assert LIMITS.workspace_min[axis] <= ee[axis] <= LIMITS.workspace_max[axis]
    assert np.linalg.norm(ee) <= 0.855 + 0.25  # reach + flange/hand offset
    assert not np.allclose(ee, fk_ee_pos(step(HOME, 1, HOME[1] + 0.3)[:7]))


def test_episode_timer_restarts_after_idle_gap():
    """BG-2: the wall timer spans one episode — a command gap of at least
    idle_reset_s (a reset/idle boundary) restarts it."""
    first, elapsed = episode_elapsed(first_t=0.0, last_t=50.0, now=50.1, idle_reset_s=2.0)
    assert first == 0.0 and elapsed == pytest.approx(50.1)
    first, elapsed = episode_elapsed(first_t=0.0, last_t=50.0, now=55.0, idle_reset_s=2.0)
    assert first == 55.0 and elapsed == 0.0


def test_unsupported_embodiment_is_refused_at_startup():
    """BG-2: an embodiment with no limits section (so101 until its asset
    and kinematics land) is refused loudly at startup, never guessed."""
    with pytest.raises(ValueError, match="so101"):
        load_limits("so101")


def test_workspace_intent_reported_even_when_velocity_contains_it():
    """BG-2: the workspace check applies to the COMMANDED pose — a command
    whose FK is below the floor is reported as a workspace violation even
    when the velocity clamp already shortened the actual step to a legal
    one (the intent was out-of-workspace; the metrics must see it)."""
    assert fk_ee_pos(DIVING[:7])[2] < LIMITS.workspace_min[2]
    safe, violations = clamp_joint_cmd(DIVING, HOME, LIMITS, timed_out=False)
    reasons = [v["reason"] for v in violations]
    assert "workspace" in reasons and "velocity" in reasons
    ee = fk_ee_pos(safe[:7])
    for axis in range(3):
        assert LIMITS.workspace_min[axis] - 1e-6 <= ee[axis] <= LIMITS.workspace_max[axis] + 1e-6
    workspace = next(v for v in violations if v["reason"] == "workspace")
    assert workspace["axis"] == "z"
    assert workspace["requested"] == pytest.approx(float(fk_ee_pos(DIVING[:7])[2]), abs=1e-5)


def test_fallback_qpos_matches_scene_home():
    """BG-2 (simplify review): env/limits.toml fallback_qpos and the frozen
    scene home_qpos are the same pose in two frozen files — assert they
    never drift apart."""
    from aisle.scenes.pharmacy import load_physics

    assert list(LIMITS.fallback_qpos) == list(load_physics()["embodiment"]["franka"]["home_qpos"])
