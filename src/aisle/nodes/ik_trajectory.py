"""ik-trajectory node (CAP-5): grasp_pose + joint_state -> joint_cmd/gripper_cmd.

Staged pick-and-place executor: pregrasp along the grasp's approach axis,
descend, close, lift, transfer over the tray, lower, release, home.
Waypoints are solved with damped-least-squares IK on the shared Panda
kinematics (budget_guard.fk_flange) — pure numpy, deterministic (CON-5),
no sim. Commands stream at the joint_state cadence, velocity-bounded per
the manifest's max_joint_vel_rad_s. A reset aborts any active plan
(episode boundary — stale plans froze the guard in the first live run).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from aisle.nodes.budget_guard import fk_flange, load_limits

# Panda hand: flange plate -> TCP between the fingertips
TCP_OFFSET = 0.1034
STAGES = (
    "rise",
    "staging",
    "pregrasp",
    "advance",
    "close",
    "lift",
    "retract",
    "transfer",
    "lower",
    "release",
    "clear",
    "home",
)
# small vertical lift right after closing, before retracting: front-mode
# boxes must rise off their board but stay under the board above
LIFT_H = 0.015
# stage-completion tracking tolerance (rad) and bounded at-target dwell (s)
TRACK_TOL = 0.10
STAGE_BAIL_S = 4.0
# gripper ramp per 100 Hz tick and message cadence: emission is
# 100/GRIP_SEND_EVERY = 25 Hz (the gripper_cmd contract is <=30 Hz;
# every-3rd-tick's 33.3 Hz was illegal) and the per-message step
# (GRIP_SEND_EVERY * GRIP_STEP_PER_TICK = 0.04) stays <= the guard's
# gripper_rate_max * gripper_dt_s bound; both relations are pinned by
# tests/unit/test_ik_trajectory.py
GRIP_STEP_PER_TICK = 0.010
GRIP_SEND_EVERY = 4


def grip_ramp_tick(current: float, target: float, tick: int) -> tuple[float, int, bool]:
    """One 100 Hz tick of the gripper ramp: returns (grip, tick, emit).
    Pure so the emitted SEQUENCE is testable: per-message step legality
    and emission cadence both regressed during review rounds 1-2."""
    if current == target:
        return current, tick, False
    step = min(GRIP_STEP_PER_TICK, abs(target - current))
    current = current + step if target > current else current - step
    tick += 1
    return current, tick, tick % GRIP_SEND_EVERY == 0 or current == target


# max per-joint jump between consecutive insertion waypoints (rad)
CONTINUITY_MAX = 1.2
# max per-joint jump for the front-mode wrist flip, held to the same
# bound as every other consecutive pair: multi-radian flips have NEVER
# executed stably (a ~2.5 rad planned flip diverged to 3.4 rad tracking
# error and wrapped the arm into a physics NaN that CRASHED the bridge —
# T09 diag runs). Until the under-board grasp strategy is resolved
# (ADR-10 section 8), an over-limit flip REFUSES the plan so the episode
# closes honestly via the verifier timeout instead of killing the sim
FLIP_MAX = CONTINUITY_MAX
# staging TCP height: above every shelf box top (max 0.57), reached BEFORE
# moving over the scene — the raw home->pregrasp joint sweep clipped shelf
# boxes (T08)
STAGING_Z = 0.66
# TCP height for the lowering stage: tray base top (0.04) + tallest med
# half-extent (0.055) + finger clearance
# fallback release TCP height when grasp_pose metadata carries no per-med
# place_tcp_z (planner computes: tray top + hanging box length + drop gap
# — pressing the box down drove it THROUGH the tray slab, hovering high
# toppled it)
PLACE_TCP_Z = 0.125
TRANSFER_TCP_Z = 0.30

_LIMITS = load_limits("franka")
_Q_MIN = np.asarray(_LIMITS.q_min[:7], dtype=np.float64)
_Q_MAX = np.asarray(_LIMITS.q_max[:7], dtype=np.float64)
_RZ_PI = np.diag([-1.0, -1.0, 1.0])  # local z spin: box-symmetric grasp flip
# canonical retry seeds (CON-5: a FIXED list, tried in order): DLS from the
# home posture stalls in a local minimum for horizontal-wrist targets; a
# wrist-forward posture reaches them in <150 iterations
_CANONICAL_SEEDS = (
    np.array([0.0, 0.2, 0.0, -2.6, 0.0, 1.2, 0.785], dtype=np.float32),
    np.array([0.0, -0.4, 0.0, -2.8, 0.0, 2.4, 0.785], dtype=np.float32),
)


def fk_tcp(q_arm: np.ndarray) -> np.ndarray:
    pos, rotation = fk_flange(q_arm)
    return pos + rotation[:, 2] * TCP_OFFSET


def quat_to_rotation(quat_xyzw) -> np.ndarray:
    x, y, z, w = (float(v) for v in quat_xyzw)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def rotation_to_quat(rotation: np.ndarray) -> np.ndarray:
    """Matrix -> xyzw quaternion (Shepperd, branch-stable)."""
    r = rotation
    trace = np.trace(r)
    if trace > 0:
        w = math.sqrt(1.0 + trace) / 2
        x, y, z = (
            (r[2, 1] - r[1, 2]) / (4 * w),
            (r[0, 2] - r[2, 0]) / (4 * w),
            (r[1, 0] - r[0, 1]) / (4 * w),
        )
        return np.array([x, y, z, w])
    i = int(np.argmax(np.diag(r)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(max(1.0 + r[i, i] - r[j, j] - r[k, k], 1e-12)) * 2
    vec = [0.0, 0.0, 0.0]
    vec[i] = s / 4
    vec[j] = (r[j, i] + r[i, j]) / s
    vec[k] = (r[k, i] + r[i, k]) / s
    w = (r[k, j] - r[j, k]) / s
    return np.array([*vec, w])


def _slerp(qa: np.ndarray, qb: np.ndarray, t: float) -> np.ndarray:
    if float(np.dot(qa, qb)) < 0:
        qb = -qb
    dot = min(1.0, max(-1.0, float(np.dot(qa, qb))))
    theta = math.acos(dot)
    if theta < 1e-6:
        return qa
    return (math.sin((1 - t) * theta) * qa + math.sin(t * theta) * qb) / math.sin(theta)


def topdown_rotation(yaw: float) -> np.ndarray:
    """Rz(yaw) @ Rx(pi): flange z straight down."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
    return rz @ rx


def _rotation_error(current: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Quaternion-based orientation error (2*sign(w)*vec of target@current.T).

    The naive skew-symmetric rotation-vector (0.5*vee(R_err)) is ZERO at a
    180-degree error (sin(pi)=0) — the first live T08 runs 'converged' onto
    a pi-flipped wrist because of exactly that blindness. The quaternion
    vector part is sin(theta/2)*axis: non-degenerate at pi."""
    r = target @ current.T
    trace = np.trace(r)
    # Shepperd's method, branch-stable
    if trace > 0:
        w = math.sqrt(1.0 + trace) / 2
        vec = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) / (4 * w)
    else:
        i = int(np.argmax(np.diag(r)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(max(1.0 + r[i, i] - r[j, j] - r[k, k], 1e-12)) * 2
        vec = np.empty(3)
        vec[i] = s / 4
        vec[j] = (r[j, i] + r[i, j]) / s
        vec[k] = (r[k, i] + r[i, k]) / s
        w = (r[k, j] - r[j, k]) / s
    return 2.0 * (vec if w >= 0 else -vec)


def _pose_error(q: np.ndarray, target_pos: np.ndarray, target_rot: np.ndarray) -> np.ndarray:
    pos, rotation = fk_flange(q)
    tcp = pos + rotation[:, 2] * TCP_OFFSET
    return np.concatenate([target_pos - tcp, _rotation_error(rotation, target_rot)])


def _dls(q: np.ndarray, target_pos, target_rot, rows: slice, iters: int) -> np.ndarray:
    """Damped-least-squares descent on the selected error rows (position
    rows only for the bootstrap, all six for the full solve). Clamped
    error (CLIK-style) plus a deterministic backtracking line search keep
    the descent stable near joint limits, where the raw DLS oscillates."""
    damping = 0.05
    eps = 1e-5
    n = rows.stop - rows.start
    reg = damping**2 * np.eye(n)
    err = _pose_error(q, target_pos, target_rot)[rows]
    for _ in range(iters):
        clamped = err.copy()
        pos_norm = np.linalg.norm(clamped[:3])
        if pos_norm > 0.08:
            clamped[:3] *= 0.08 / pos_norm
        if n == 6:
            rot_norm = np.linalg.norm(clamped[3:])
            if rot_norm > 0.4:
                clamped[3:] *= 0.4 / rot_norm
        jac = np.empty((n, 7))
        for j in range(7):
            dq = q.copy()
            dq[j] += eps
            jac[:, j] = (err - _pose_error(dq, target_pos, target_rot)[rows]) / eps
        step = jac.T @ np.linalg.solve(jac @ jac.T + reg, clamped)
        base_norm = np.linalg.norm(err)
        for _halving in range(4):
            candidate = np.clip(q + step, _Q_MIN, _Q_MAX)
            candidate_err = _pose_error(candidate, target_pos, target_rot)[rows]
            if np.linalg.norm(candidate_err) < base_norm:
                break
            step = step / 2
        q = candidate
        err = candidate_err  # carry: next iteration reuses the accepted error
    return q


def _ik_once(target_pos: np.ndarray, target_rot: np.ndarray, q0: np.ndarray) -> np.ndarray | None:
    q = np.asarray(q0, dtype=np.float64).copy()
    for bootstrap in (False, True):
        if bootstrap:
            # position-only descent first: pulls the arm into the right
            # region, where the full-pose solve has a clean basin
            q = _dls(
                np.asarray(q0, dtype=np.float64).copy(), target_pos, target_rot, slice(0, 3), 60
            )
        q = _dls(q, target_pos, target_rot, slice(0, 6), 150)
        err = _pose_error(q, target_pos, target_rot)
        if np.linalg.norm(err[:3]) < 5e-4 and np.linalg.norm(err[3:]) < 1e-3:
            return q.astype(np.float32)
    return None


def ik_continuation(
    from_pos: np.ndarray,
    to_pos: np.ndarray,
    target_rot: np.ndarray,
    q_start: np.ndarray,
    step_m: float = 0.04,
) -> list[np.ndarray] | None:
    """Solve a Cartesian straight-line move by numerical continuation and
    return EVERY substep config: the executor tracks the planned line
    through these waypoints (discarding them and joint-interpolating
    endpoint-to-endpoint lets the TCP bow off the line — PR #10 review),
    and chaining keeps each config on q_start's branch (a single far
    solve can land wrist-flipped and sweep the arm through the shelf)."""
    from_pos = np.asarray(from_pos, dtype=np.float64)
    to_pos = np.asarray(to_pos, dtype=np.float64)
    n = max(1, int(math.ceil(np.linalg.norm(to_pos - from_pos) / step_m)))
    q = q_start
    path: list[np.ndarray] = []
    for i in range(1, n + 1):
        q = _ik_once(from_pos + (to_pos - from_pos) * (i / n), target_rot, q)
        if q is None:
            return None
        path.append(q)
    return path


def ik_solve(target_pos: np.ndarray, target_rot: np.ndarray, q0: np.ndarray) -> np.ndarray | None:
    """DLS-IK for a TCP pose. Deterministic (CON-5): fixed seed pose, fixed
    iteration budget; a box is symmetric under a 180-degree spin about the
    approach axis, so the flipped grasp is tried in fixed order before
    reporting failure."""
    target_pos = np.asarray(target_pos, dtype=np.float64)
    for rot in (target_rot, target_rot @ _RZ_PI):
        for seed in (q0, *_CANONICAL_SEEDS):
            q = _ik_once(target_pos, rot, seed)
            if q is not None:
                return q
    return None


def interpolate_step(
    current: np.ndarray, target: np.ndarray, max_vel: float, dt: float
) -> np.ndarray:
    """One velocity-bounded step of joint-space interpolation."""
    delta = np.clip(target - current, -max_vel * dt, max_vel * dt)
    return (current + delta).astype(np.float32)


@dataclass(frozen=True)
class Stage:
    name: str
    path: tuple  # (n, 7) waypoint chain; the LAST entry is the stage target
    gripper: float  # 0 open .. 1 closed
    settle_s: float  # dwell after reaching, letting physics catch up
    vel: float = 1.0  # joint-velocity scale; carry stages move gently

    @property
    def q(self) -> np.ndarray:
        return self.path[-1]


class StagedPlan:
    """The full pick-place waypoint sequence, solved once per grasp_pose.
    Chained seeding (each stage seeds the next solve) keeps arm
    configurations consistent across stages. The pregrasp sits
    approach_height back along the grasp's own approach axis, so a tilted
    grasp descends along its tilt."""

    def __init__(
        self,
        grasp_pose: np.ndarray,
        tray_xy: tuple[float, float],
        approach_m: float,
        q_seed: np.ndarray,
        place_z: float = PLACE_TCP_Z,
    ) -> None:
        grasp_pose = np.asarray(grasp_pose, dtype=np.float32).reshape(7)
        grasp_pos = grasp_pose[:3].astype(np.float64)
        grasp_rot = quat_to_rotation(grasp_pose[3:7])
        approach_axis = grasp_rot[:, 2]  # flange z: points from wrist to fingertips
        home = np.asarray(q_seed, dtype=np.float32)[:7]
        pre_pos = grasp_pos - approach_axis * approach_m
        up = np.array([0.0, 0.0, LIFT_H])
        self.stages: list[Stage] = []
        self.error: str | None = None

        place_rot = topdown_rotation(0.0)
        transfer_pos = np.array([tray_xy[0], tray_xy[1], TRANSFER_TCP_Z])
        lower_pos = np.array([tray_xy[0], tray_xy[1], place_z])
        # approach entirely in free space: rise vertically over the home
        # footprint, traverse at height, then descend — the raw joint-space
        # sweep from home crossed the shelf volume and clipped boxes (T08)
        home_tcp = fk_tcp(home)
        staging_z = max(STAGING_Z, float(pre_pos[2]))
        rise_pos = np.array([home_tcp[0], home_tcp[1], staging_z])
        staging_pos = np.array([pre_pos[0], pre_pos[1], staging_z])
        # a FRONT grasp holds its horizontal wrist only from the pregrasp
        # on; the high approach flies with the neutral top-down wrist (a
        # 0.5 m descent holding a horizontal wrist does not converge), and
        # the wrist flip happens in free air ahead of the shelf
        front_mode = abs(float(approach_axis[2])) < 0.5  # metadata carries the flag too
        approach_rot = topdown_rotation(0.0) if front_mode else grasp_rot
        q_rise = ik_solve(rise_pos, approach_rot, home)
        if q_rise is None:
            self.error = f"IK failed for waypoint 'rise' at {np.round(rise_pos, 3).tolist()}"
            return
        staging_path = ik_continuation(rise_pos, staging_pos, approach_rot, q_rise)
        if staging_path is None:
            self.error = f"IK failed for waypoint 'staging' at {np.round(staging_pos, 3).tolist()}"
            return
        q_staging = staging_path[-1]
        self.flip_pair: tuple | None = None
        if front_mode:
            # descend most of the way neutral, then flip the wrist to
            # horizontal. Slerped orientation continuation does NOT
            # converge through the intermediate tilts here, so the flip
            # executes as ONE joint-interpolated move whose swept hand
            # path is explicitly VERIFIED to stay in the free half-space
            # ahead of the shelf (the PR #10 review measured the raw jump
            # at 2.18 rad; unverified it could sweep anywhere)
            drop_pos = np.array([pre_pos[0], pre_pos[1], min(staging_z, pre_pos[2] + 0.15)])
            drop_path = ik_continuation(staging_pos, drop_pos, approach_rot, q_staging)
            q_pre = ik_solve(pre_pos, grasp_rot, drop_path[-1]) if drop_path is not None else None
            if drop_path is not None and q_pre is not None:
                if np.abs(q_pre - drop_path[-1]).max() > FLIP_MAX:
                    self.error = "front flip jump exceeds FLIP_MAX (infeasible reorientation)"
                    return
                limit_x = float(pre_pos[0]) + 0.04  # 2 cm shy of the shelf front
                for f in np.linspace(0.0, 1.0, 21):
                    q_sweep = drop_path[-1] + f * (q_pre - drop_path[-1])
                    flange_pos, rotation = fk_flange(q_sweep)
                    tcp = flange_pos + rotation[:, 2] * TCP_OFFSET
                    if tcp[0] > limit_x or flange_pos[0] > limit_x:
                        self.error = "front flip sweep enters the shelf half-space"
                        return
                self.flip_pair = (drop_path[-1], q_pre)
            pregrasp_path = drop_path + [q_pre] if q_pre is not None else None
        else:
            pregrasp_path = ik_continuation(staging_pos, pre_pos, grasp_rot, q_staging)
        if pregrasp_path is None:
            self.error = f"IK failed for waypoint 'pregrasp' at {np.round(pre_pos, 3).tolist()}"
            return
        q_pre = pregrasp_path[-1]
        # the insertion (advance/lift/retract) and placement descents are
        # continuation PATHS the executor follows waypoint by waypoint
        advance_path = ik_continuation(pre_pos, grasp_pos, grasp_rot, q_pre)
        lift_path = (
            ik_continuation(grasp_pos, grasp_pos + up, grasp_rot, advance_path[-1])
            if advance_path is not None
            else None
        )
        retract_path = (
            ik_continuation(grasp_pos + up, pre_pos + up, grasp_rot, lift_path[-1])
            if lift_path is not None
            else None
        )
        q_transfer = (
            ik_solve(transfer_pos, place_rot, retract_path[-1])
            if retract_path is not None
            else None
        )
        lower_path = (
            ik_continuation(transfer_pos, lower_pos, place_rot, q_transfer)
            if q_transfer is not None
            else None
        )
        # the fingers open WHILE the TCP rises off the seated box: opening
        # in place shears the top-held box over even when it is seated
        # (fingertips drag on the box faces under residual squeeze)
        release_path = (
            ik_continuation(
                lower_pos, lower_pos + np.array([0.0, 0.0, 0.05]), place_rot, lower_path[-1]
            )
            if lower_path is not None
            else None
        )
        if release_path is None:
            self.error = "IK failed along the insertion or placement path"
            return
        self.stages = [
            Stage("rise", (q_rise,), 0.0, 0.1),
            Stage("staging", tuple(staging_path), 0.0, 0.1),
            Stage("pregrasp", tuple(pregrasp_path), 0.0, 0.2),
            Stage("advance", tuple(advance_path), 0.0, 0.3),
            Stage("close", (advance_path[-1],), 1.0, 0.5),
            Stage("lift", tuple(lift_path), 1.0, 0.2, vel=0.5),
            Stage("retract", tuple(retract_path), 1.0, 0.2, vel=0.5),
            # the transfer swing is where the box shifts in the grip:
            # carry it gently
            Stage("transfer", (q_transfer,), 1.0, 0.3, vel=0.35),
            Stage("lower", tuple(lower_path), 1.0, 0.3, vel=0.35),
            Stage("release", tuple(release_path), 0.0, 0.5, vel=0.35),
            # rise clear of the tray walls before the home swing: the raw
            # release->home sweep dragged the fingers through the tray and
            # jammed the arm (T08 live run)
            Stage("clear", (q_transfer,), 0.0, 0.1),
            Stage("home", (home,), 0.0, 0.0),
        ]
        # continuity invariant over every consecutive waypoint pair of the
        # SHELF-PROXIMATE stages (rise..retract, incl. within-path steps
        # and the front-mode wrist flip): a branch flip there sweeps the
        # arm through the shelf. The transfer/home swings are deliberate
        # large free-space moves over open ground and are exempt.
        flat = [q for stage in self.stages[:7] for q in stage.path]
        for a, b in zip(flat, flat[1:], strict=False):
            if self.flip_pair is not None and a is self.flip_pair[0] and b is self.flip_pair[1]:
                continue  # the flip jump is sweep-verified above instead
            if np.abs(np.asarray(a) - np.asarray(b)).max() > CONTINUITY_MAX:
                self.error = "discontinuous waypoint chain"
                self.stages = []
                return

    @property
    def ok(self) -> bool:
        return not self.error


def main() -> None:
    import os
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import load_physics, resolve_layout
    from aisle.topics import make_sender

    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    physics = load_physics()
    layout = resolve_layout(physics, embodiment)
    tray_pos = layout["tray"]["pos"]
    tray_xy = (float(tray_pos[0]), float(tray_pos[1]))
    home = np.asarray(physics["embodiment"][embodiment]["home_qpos"], dtype=np.float32)
    n_arm = 7
    max_vel = float(os.environ.get("AISLE_MAX_JOINT_VEL", "1.0"))
    dt = 0.01  # joint_state contract cadence (TC-4)

    node = Node()
    send = make_sender(node)
    plan: StagedPlan | None = None
    stage_idx = 0
    wp_idx = 0
    settle_ticks = 0
    at_target_ticks = 0
    current_cmd: np.ndarray | None = None
    integ = np.zeros(n_arm, dtype=np.float32)
    current_grip = 0.0
    grip_tick = 0

    def clear_plan() -> None:
        nonlocal plan, stage_idx, wp_idx, settle_ticks, at_target_ticks, current_cmd
        nonlocal current_grip, grip_tick
        plan = None
        stage_idx = 0
        wp_idx = 0
        settle_ticks = 0
        at_target_ticks = 0
        current_cmd = None
        current_grip = 0.0
        grip_tick = 0
        integ[:] = 0.0

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "reset_done":
            # episode boundary: NEVER keep executing a stale plan — in the
            # first live run a stale stream fought the post-reset guard
            # reference until the wall timeout froze everything
            clear_plan()
        elif event["id"] == "grasp_pose":
            if plan is not None and stage_idx < len(plan.stages):
                continue  # one plan at a time; re-plan only after home
            candidate = StagedPlan(
                event["value"].to_numpy(zero_copy_only=False),
                tray_xy,
                float(metadata.get("approach_m", 0.15)),
                home,
                place_z=float(metadata.get("place_tcp_z", PLACE_TCP_Z)),
            )
            if not candidate.ok:
                print(f"grasp plan failed: {candidate.error}", file=sys.stderr)
                continue
            plan = candidate
            stage_idx = 0
            wp_idx = 0
            settle_ticks = 0
            at_target_ticks = 0
            print(f"plan ready: {len(plan.stages)} stages", file=sys.stderr)
        elif event["id"] == "joint_state" and plan is not None and stage_idx < len(plan.stages):
            qpos = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if current_cmd is None:
                current_cmd = qpos[:n_arm].copy()
            stage = plan.stages[stage_idx]
            # ramp the gripper (the emitted sequence is unit-tested via
            # grip_ramp_tick)
            current_grip, grip_tick, emit = grip_ramp_tick(current_grip, stage.gripper, grip_tick)
            if emit:
                send(
                    "gripper_cmd",
                    pa.array(np.array([current_grip], dtype=np.float32)),
                    metadata,
                )
            # march the stage's waypoint chain: track the PLANNED Cartesian
            # path, not a straight joint-space line between stage endpoints
            waypoint = stage.path[wp_idx]
            current_cmd = interpolate_step(current_cmd, waypoint, max_vel * stage.vel, dt)
            if wp_idx < len(stage.path) - 1 and np.abs(current_cmd - waypoint).max() < 1e-6:
                wp_idx += 1
            # integral correction: the MJCF actuators sag ~0.08 rad under
            # gravity (their gains are baked into the asset), which is
            # centimeters at the TCP — integrate the tracking error into
            # the COMMAND so the sim settles on the true target
            integ = np.clip(integ + 0.004 * (current_cmd - qpos[:n_arm]), -0.15, 0.15)
            corrected = np.clip(current_cmd + integ, _Q_MIN, _Q_MAX).astype(np.float32)
            # finger targets FOLLOW the stage's gripper intent: the bridge
            # applies commands last-wins across all dofs (BRG-1), so a
            # joint_cmd carrying live qpos fingers would overwrite the
            # close-gripper target every 10 ms and the grip would never
            # close
            fingers = (home[n_arm:] * (1.0 - current_grip)).astype(np.float32)
            full_cmd = np.concatenate([corrected, fingers]).astype(np.float32)
            send("joint_cmd", pa.array(full_cmd), metadata)
            # stage completion: command reached target AND the sim tracked
            # it within tolerance (PD steady-state at horizontal reach sits
            # near 0.1 rad); a bounded at-target dwell advances anyway so a
            # contact-blocked or draggy joint cannot stall the plan forever
            if np.abs(current_cmd - stage.q).max() < 1e-6 and current_grip == stage.gripper:
                at_target_ticks += 1
                track_err = np.abs(qpos[:n_arm] - stage.q)
                tracked = track_err.max() < TRACK_TOL
                if tracked:
                    settle_ticks += 1
                if settle_ticks * dt >= stage.settle_s or at_target_ticks * dt >= STAGE_BAIL_S:
                    if not tracked:
                        print(
                            f"stage {stage.name} bailed at joint {int(track_err.argmax())} "
                            f"err {float(track_err.max()):.3f}",
                            file=sys.stderr,
                        )
                    print(f"stage done: {stage.name}", file=sys.stderr)
                    stage_idx += 1
                    wp_idx = 0
                    settle_ticks = 0
                    at_target_ticks = 0
                    if stage_idx >= len(plan.stages):
                        clear_plan()  # finished: idle until the next episode


if __name__ == "__main__":
    main()
