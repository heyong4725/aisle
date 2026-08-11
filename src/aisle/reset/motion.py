"""Behavioral-reset motion (SPEC 040 RST-2) — pick from tray, place at
the sampled shelf pose, all sensing REALISTIC.

Self-contained inside the frozen boundary by design (PR-1 note + owner
sign-off): only frozen imports — budget_guard kinematics, scene config,
the verifier's pinned detector and backprojection. Importing the
unfrozen policy planner here would let an agent alter reset behavior
through a file the env hash does not cover, so the minimal motion is
REIMPLEMENTED: a finite-difference DLS onto budget_guard.fk_flange, a
five-waypoint top-down pick/place, and a compact position streamer.

Sensing is the realistic pipeline only (VAL-6: oracle_state never
reaches reset): the tray pick pose comes from the pinned detector +
sensor-depth backprojection, and the placement is verified the same way
before the attempt reports success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aisle.nodes.budget_guard import fk_flange, gripper_to_fingers, load_limits

# Panda hand: flange plate -> TCP between the fingertips (same constant
# the policy executor uses; restated here because this module must not
# import unfrozen code)
TCP_OFFSET = 0.1034
HAND_MOUNT_YAW = -math.pi / 4
# staging TCP height above everything on the desk (clears the tallest
# shelf box at 0.44)
STAGING_Z = 0.56
# grip engagement below the ESTIMATED box top, and the fingertip hover
# for release. Deep on purpose: the depth-derived top z runs ~2 cm HIGH
# (measured: est 0.130 vs true 0.110 top), and a shallow grip at the
# estimate grazed the box and slipped — the tray-floor guard in
# place_back_stages keeps the deep engagement off the tray slab
GRIP_ENGAGE = 0.045
TRAY_CLEAR = 0.015  # fingertips never below tray_top + this
PLACE_DROP_GAP = 0.012
# Cartesian waypoint spacing for segment subdivision (see
# place_back_stages: joint-space hops between segment endpoints swept
# the box out of the tray)
SEGMENT_STEP_M = 0.05
# realistic placement acceptance: detector centre within this of the
# sampled slot (the verifier's identity stages localize to ~1-2 cm)
PLACE_TOL_M = 0.05


def topdown_rotation(yaw: float = 0.0) -> np.ndarray:
    """Flange z straight down, finger axis at `yaw` (mount-compensated —
    the same convention the policy planner measured in issue #92)."""
    cy, sy = math.cos(yaw + HAND_MOUNT_YAW), math.sin(yaw + HAND_MOUNT_YAW)
    rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
    return rz @ rx


def _pose_error(q: np.ndarray, target_pos: np.ndarray, target_rot: np.ndarray) -> np.ndarray:
    pos, rot = fk_flange(q)
    tcp = pos + rot[:, 2] * TCP_OFFSET
    r = target_rot @ rot.T
    trace = float(np.trace(r))
    w = math.sqrt(max(1.0 + trace, 1e-12)) / 2.0
    vec = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) / (4.0 * w)
    return np.concatenate([target_pos - tcp, 2.0 * w * vec])


def solve_ik(
    target_pos: np.ndarray,
    target_rot: np.ndarray,
    q0: np.ndarray,
    iters: int = 200,
    tol: float = 2e-3,
) -> np.ndarray | None:
    """Damped least squares with a finite-difference Jacobian over the
    frozen fk (CON-5: fixed seed pose, fixed iteration budget). Compact
    on purpose — reset poses are tray-top and shelf-front top-downs,
    the best-conditioned region of the workspace."""
    limits = load_limits("franka")
    lower = np.asarray(limits.q_min_arr[:7], dtype=np.float64)
    upper = np.asarray(limits.q_max_arr[:7], dtype=np.float64)
    q = np.clip(np.asarray(q0, dtype=np.float64).copy(), lower, upper)
    eps = 1e-5
    for _ in range(iters):
        err = _pose_error(q, target_pos, target_rot)
        if float(np.linalg.norm(err[:3])) < tol and float(np.linalg.norm(err[3:])) < 5e-3:
            return q.astype(np.float32)
        jac = np.empty((6, 7))
        for j in range(7):
            dq = q.copy()
            dq[j] += eps
            jac[:, j] = (_pose_error(dq, target_pos, target_rot) - err) / -eps
        step = jac.T @ np.linalg.solve(jac @ jac.T + 1e-4 * np.eye(6), err)
        q = np.clip(q + np.clip(step, -0.2, 0.2), lower, upper)
    return None


@dataclass(frozen=True)
class ResetStage:
    name: str
    q: np.ndarray
    gripper: float  # 0 open .. 1 closed
    settle_s: float


def place_back_stages(
    top_pos: np.ndarray,
    tray_top_z: float,
    place_pos: np.ndarray,
    place_board_z: float,
    home_q: np.ndarray,
) -> list[ResetStage] | None:
    """The five-phase return: stage above the tray, descend to grip
    depth, close, carry high to above the slot, lower, release, home.
    Fully position-derived (no identity, no size table): `top_pos` is
    the REALISTIC top-surface point from locate_box_in_tray, so box
    height = top z - tray top, and every grip/place height follows.
    None when any waypoint has no IK — the attempt fails honestly and
    the service's fallback teleports."""
    rot = topdown_rotation(0.0)
    q = home_q[:7].astype(np.float64)
    stages: list[ResetStage] = []
    box_height = max(float(top_pos[2]) - tray_top_z, 0.02)
    pick_pos = np.asarray(top_pos, dtype=np.float64)
    grip_z = max(float(top_pos[2]) - GRIP_ENGAGE, tray_top_z + TRAY_CLEAR)
    grip_depth = float(top_pos[2]) - grip_z  # actual engagement below est top
    place_z = place_board_z + box_height - grip_depth + PLACE_DROP_GAP
    last_pos: np.ndarray | None = None
    for name, pos, grip, settle in (
        ("stage-tray", np.array([pick_pos[0], pick_pos[1], STAGING_Z]), 0.0, 0.2),
        ("descend", np.array([pick_pos[0], pick_pos[1], grip_z]), 0.0, 0.4),
        ("close", np.array([pick_pos[0], pick_pos[1], grip_z]), 1.0, 0.6),
        ("lift", np.array([pick_pos[0], pick_pos[1], STAGING_Z]), 1.0, 0.2),
        ("carry", np.array([place_pos[0], place_pos[1], STAGING_Z]), 1.0, 0.3),
        ("lower", np.array([place_pos[0], place_pos[1], place_z]), 1.0, 0.4),
        ("release", np.array([place_pos[0], place_pos[1], place_z]), 0.0, 0.6),
        ("clear", np.array([place_pos[0], place_pos[1], STAGING_Z]), 0.0, 0.2),
    ):
        # subdivide every Cartesian segment into branch-continuous IK
        # waypoints (each seeded from the previous solution): a straight
        # joint interpolation between segment endpoints reconfigures the
        # arm mid-air — the descending hand SWEPT the box 5 cm out of
        # the tray before the fingers ever closed (offline repro)
        targets = [np.asarray(pos, dtype=np.float64)]
        if last_pos is not None:
            span = float(np.linalg.norm(pos - last_pos))
            n_steps = max(1, int(math.ceil(span / SEGMENT_STEP_M)))
            targets = [last_pos + (pos - last_pos) * (k / n_steps) for k in range(1, n_steps + 1)]
        for i, waypoint in enumerate(targets):
            solved = solve_ik(waypoint, rot, q)
            if solved is None:
                return None
            final = i == len(targets) - 1
            stages.append(
                ResetStage(
                    name=name if final else f"{name}~{i}",
                    q=solved,
                    gripper=grip,
                    settle_s=settle if final else 0.0,
                )
            )
            q = solved.astype(np.float64)
        last_pos = np.asarray(pos, dtype=np.float64)
    stages.append(
        ResetStage(name="home", q=home_q[:7].astype(np.float32), gripper=0.0, settle_s=0.3)
    )
    return stages


@dataclass(kw_only=True)
class ResetStreamer:
    """Compact position streamer at the joint_state cadence: velocity-
    bounded interpolation toward the stage target, gripper ramped, dwell
    to settle, bounded bail so a blocked joint cannot hang the reset."""

    stages: list[ResetStage]
    dt: float = 0.01
    max_vel: float = 1.0
    grip_step: float = 0.01
    bail_s: float = 4.0
    stage_idx: int = field(default=0)
    settle_ticks: int = field(default=0)
    at_target_ticks: int = field(default=0)
    current: np.ndarray | None = field(default=None)
    grip: float = field(default=0.0)

    @property
    def done(self) -> bool:
        return self.stage_idx >= len(self.stages)

    def step(self, qpos: np.ndarray) -> tuple[np.ndarray | None, float | None]:
        """-> (full joint command incl. fingers, gripper value) or Nones
        when finished."""
        if self.done:
            return None, None
        limits = load_limits("franka")
        stage = self.stages[self.stage_idx]
        if self.current is None:
            self.current = np.asarray(qpos[:7], dtype=np.float64).copy()
        step = np.clip(
            stage.q.astype(np.float64) - self.current,
            -self.max_vel * self.dt,
            self.max_vel * self.dt,
        )
        self.current = self.current + step
        grip_out = None
        if self.grip != stage.gripper:
            delta = np.clip(stage.gripper - self.grip, -self.grip_step, self.grip_step)
            self.grip = float(self.grip + delta)
            grip_out = self.grip
        fingers = gripper_to_fingers(self.grip, limits).astype(np.float32)
        cmd = np.concatenate([self.current.astype(np.float32), fingers])
        if float(np.abs(self.current - stage.q).max()) < 1e-6 and self.grip == stage.gripper:
            self.at_target_ticks += 1
            tracked = float(np.abs(np.asarray(qpos[:7]) - stage.q).max()) < 0.10
            if tracked:
                self.settle_ticks += 1
            if (
                self.settle_ticks * self.dt >= stage.settle_s
                or self.at_target_ticks * self.dt >= self.bail_s
            ):
                self.stage_idx += 1
                self.settle_ticks = 0
                self.at_target_ticks = 0
        return cmd, grip_out


# detector scores at the tray run low (measured 0.05 on the delivered
# box from overhead — the shallow view foreshortens the face the
# color-word queries anchor on); the floor only needs to clear
# background clutter inside the SMALL tray footprint
TRAY_SCORE_FLOOR = 0.03


def locate_box_in_tray(
    rgb: np.ndarray,
    depth: np.ndarray,
    calibration: dict,
    med_names: list[str],
    tray_cfg: dict,
    model_pair=None,
) -> np.ndarray | None:
    """REALISTIC tray localization: best detection (LABEL DISCARDED —
    positions are signal, identity claims are noise, the T2 lesson)
    whose backprojected centre lies inside the tray footprint. The desk
    tray holds at most the ONE delivered box, so position IS identity
    here; multi-box trays (S1) would need the label word. None = not
    found (attempt fails, fallback teleports)."""
    from aisle.verifier.models import detect_meds
    from aisle.verifier.stages import backproject_overhead

    tray_pos = np.asarray(tray_cfg["pos"], dtype=np.float64)
    tray_half = np.asarray(tray_cfg["size"], dtype=np.float64) / 2.0
    height, width = depth.shape[:2]
    best: tuple[float, np.ndarray] | None = None
    for det in detect_meds(rgb, med_names, model_pair=model_pair):
        if det["score"] < TRAY_SCORE_FLOOR:
            continue
        x0, y0, x1, y1 = (int(round(v)) for v in det["box"])
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        crop = depth[y0:y1, x0:x1]
        valid = crop > 0
        if not valid.any():
            continue
        # TOP-SURFACE centroid, not bbox centre: the overhead camera
        # looks down, so the box top is the SMALLEST depth in the crop;
        # the bbox centre includes perspective side faces and measured
        # ~2 cm off — enough for the closing fingers to knock the box
        # out of the tray instead of gripping it
        top = float(crop[valid].min())
        ys, xs = np.nonzero(valid & (crop <= top + 0.01))
        pixel = np.array([[x0 + float(xs.mean()), y0 + float(ys.mean())]])
        point = np.asarray(backproject_overhead(depth, calibration, pixel))[0]
        if (
            abs(point[0] - tray_pos[0]) <= tray_half[0]
            and abs(point[1] - tray_pos[1]) <= tray_half[1]
        ):
            if best is None or det["score"] > best[0]:
                best = (det["score"], point)
    return None if best is None else best[1]


def placement_verified(
    rgb: np.ndarray,
    depth: np.ndarray,
    calibration: dict,
    med_names: list[str],
    place_pos: np.ndarray,
    model_pair=None,
) -> bool:
    """RST-2's realistic verification: a box is detected with its
    backprojected centre within PLACE_TOL_M of the sampled slot (label
    discarded — the placed box is the one the reset just carried)."""
    from aisle.verifier.models import detect_meds
    from aisle.verifier.stages import backproject_overhead

    place = np.asarray(place_pos, dtype=np.float64)
    for det in detect_meds(rgb, med_names, model_pair=model_pair):
        if det["score"] < TRAY_SCORE_FLOOR:
            continue
        x0, y0, x1, y1 = det["box"]
        pixel = np.array([[(x0 + x1) / 2.0, (y0 + y1) / 2.0]])
        point = np.asarray(backproject_overhead(depth, calibration, pixel))[0]
        if float(np.linalg.norm(point[:2] - place[:2])) <= PLACE_TOL_M:
            return True
    return False
