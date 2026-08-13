"""Navigation nav_goal resolution (SPEC 210 MOB-2): a goal names a known
location (scenes/locations.toml) OR carries an explicit pose. Pure — no
dora, no sim."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

_LOCATIONS = Path(__file__).resolve().parents[1] / "scenes" / "locations.toml"
_LIMITS = Path(__file__).resolve().parents[3] / "env" / "limits.toml"


def load_locations() -> dict[str, list[float]]:
    """Named store-frame targets (MOB-2/MOB-5): name -> [x, y, yaw]."""
    with open(_LOCATIONS, "rb") as f:
        return {k: list(v) for k, v in tomllib.load(f)["locations"].items()}


def load_nav_params(embodiment: str) -> dict:
    """Nav-action lifecycle parameters (MOB-2) from env/limits.toml:
    arrival tolerance and the timeout/stall tick budgets."""
    with open(_LIMITS, "rb") as f:
        p = tomllib.load(f)["embodiment"][embodiment]
    return {
        "arrival_tol_m": float(p["nav_arrival_tol_m"]),
        "arrival_yaw_rad": float(p["nav_arrival_yaw_rad"]),
        "capture_tol_m": float(p["nav_capture_tol_m"]),
        "timeout_s": float(p["nav_timeout_sim_s"]),
        "stall_s": float(p["nav_stall_sim_s"]),
    }


def load_rotate_omega_max(embodiment: str) -> float:
    """The rotate-phase omega cap (see limits.toml: loop-delay overshoot
    must fit inside the arrival yaw band)."""
    with open(_LIMITS, "rb") as f:
        return float(tomllib.load(f)["embodiment"][embodiment]["nav_rotate_omega_max"])


def load_near_field_m(embodiment: str) -> float:
    """The near-field radius (see limits.toml: inside it the drive phase
    caps omega like the rotate phase, or the base orbits the target)."""
    with open(_LIMITS, "rb") as f:
        return float(tomllib.load(f)["embodiment"][embodiment]["nav_near_field_m"])


def nav_result_is_current(expected_goal_id, result_goal_id) -> bool:
    """TC-7: does this `nav_result` belong to the leg the caller is waiting
    on? Shared by every nav_result consumer (s1-expert and the S1/S3 driver
    skills) so one rule governs them all.

    Issue #179: the handlers took ANY nav_result as completing whatever was
    pending. A leg still in flight when an episode ended emitted its result
    into the NEXT episode, where it silently completed a different subtask —
    invisible in the records, because the episode looks ordinary. Matching
    the id makes that structurally impossible, and the ids do discriminate:
    `nav_seq` is monotonic for the life of the process (it is not part of
    the per-episode `clear()`), so a stale id can never collide with a live
    one.

    Fail-closed on anything unusable — a missing, None, or non-string id on
    either side is NOT a match, because "cannot tell" must not complete a
    subtask."""
    return (
        isinstance(expected_goal_id, str)
        and isinstance(result_goal_id, str)
        and expected_goal_id != ""
        and expected_goal_id == result_goal_id
    )


def resolve_nav_goal(goal: dict, locations: dict[str, list[float]]) -> list[float]:
    """Resolve a nav_goal to a store-frame [x, y, yaw] (MOB-2).

    `{"location": name}` resolves via `locations`; `{"pose": [x, y, yaw]}`
    is used verbatim. An unknown name, or a goal with neither key, is a
    loud error — the nav action must never drive to a silent default."""
    if "pose" in goal:
        pose = [float(v) for v in goal["pose"]]
        if len(pose) != 3:
            raise ValueError(f"nav_goal pose must be [x, y, yaw], got {goal['pose']!r}")
        return pose
    if "location" in goal:
        name = goal["location"]
        if name not in locations:
            raise ValueError(f"unknown location {name!r}; known: {sorted(locations)}")
        return list(locations[name])
    raise ValueError(f"nav_goal needs a 'location' or 'pose' key, got {sorted(goal)}")


class NavStateMachine:
    """Pure nav-action lifecycle (SPEC 210 MOB-2), mirroring the episode
    action (TC-7). A goal opens a nav toward a store-frame target; each
    tick emits feedback {t, dist_remaining} (>= 2 Hz) or a terminal result
    {status, failure, t_end}.

    Timeout and stall are measured in SIM seconds via the sim_time_ns
    stamps riding base_pose (PR #21 round 4, CON-5): wall-tick budgets made
    the OUTCOME depend on the machine's rtf — the same seed passed on one
    machine and timed out on another because a converging leg burned wall
    ticks, not sim time. Decisions keyed to sim stamps are a function of
    the trajectory alone. Handlers return [(topic, payload, goal_id), ...]."""

    def __init__(
        self,
        arrival_tol_m: float,
        timeout_s: float,
        stall_s: float,
        arrival_yaw_rad: float,
        capture_tol_m: float | None = None,
    ) -> None:
        self.arrival_tol_m = arrival_tol_m
        self.arrival_yaw_rad = arrival_yaw_rad
        # capture band (T15/PR #21 round 3): a drive-phase stall within this
        # radius latches the final rotate instead of failing blocked — a
        # diff-drive base cannot point-stabilize onto a target it is
        # effectively ON (mm-range bearing flips defeat the progress
        # detector). Config-sourced (nav_capture_tol_m); 1.5x arrival when
        # constructed bare.
        self.capture_tol_m = 1.5 * arrival_tol_m if capture_tol_m is None else capture_tol_m
        self.timeout_s = timeout_s
        self.stall_s = stall_s
        self.target: list[float] | None = None
        self.goal_id: str | None = None
        self._reset_goal_scoped()

    #: Every field whose lifetime is ONE nav goal, with its fresh value.
    #: `target`/`goal_id` are not here: they identify the goal rather than
    #: scope to it, and each caller sets them differently.
    #:
    #: One list, applied by `_reset_goal_scoped`, because this state is
    #: entered from three directions — construction, a new goal, and an
    #: episode boundary — and a field reset on only some of them is exactly
    #: how issue #179 (and #182's guard twin) happened. Adding a field here
    #: is the whole change; forgetting a path is no longer possible.
    _GOAL_SCOPED: tuple[tuple[str, object], ...] = (
        ("pose", None),
        ("ticks", 0),
        # consecutive unstamped poses (issue #182)
        ("_blind_poses", 0),
        # the held clock is goal-scoped: if a goal's first pose is unstamped
        # it must not anchor to the PREVIOUS goal's last stamp (PR #178)
        ("_sim_ns", None),
        ("_t0_ns", None),
        ("_progress_ns", None),
        ("_best_dist", math.inf),
        ("_best_head", math.inf),
        # rotate-only latch (T15 round 5): once inside the arrival radius
        # the base must STOP translating and only rotate — un-latched
        # drive/rotate alternation at the boundary chatters, distance never
        # improves, and the stall detector misreads it as blocked
        ("rotating", False),
    )

    def _reset_goal_scoped(self) -> None:
        for name, fresh in self._GOAL_SCOPED:
            setattr(self, name, fresh)

    def on_goal(self, target_pose: list[float], goal_id: str) -> list:
        if self.target is not None:  # TC-7: nav actions do not overlap
            return []
        self.target = [float(v) for v in target_pose]
        self.goal_id = goal_id
        self._reset_goal_scoped()
        return []

    def on_episode_boundary(self) -> str | None:
        """Abandon any in-flight goal because the EPISODE ended (issue #179).
        Returns the abandoned goal_id (None if nav was already idle).

        Without this the machine was cleared only by `on_goal` and `_finish`,
        and nothing told nav an episode had ended — so a leg still in flight
        at a timeout or verifier verdict survived the boundary and did three
        things to the next episode: its first nav_goal was silently refused
        (`on_goal` returns [] while `target` is set, TC-7 non-overlap being
        correct in isolation), the carried leg then emitted a `nav_result`
        that completed the NEW episode's subtask, and PR #178's goal-scoped
        clock reset never ran because it sits behind that same early return.

        Deliberately emits NOTHING. A `nav_result` here would carry the OLD
        goal_id into a fresh episode, which is the very confusion being
        fixed, and "the episode ended" is not one of MOB-2's failure values
        — the boundary is already in the trace as `reset_done`. The caller
        zeroes the base and logs. Returning the goal_id rather than an
        emissions list keeps it structurally impossible to feed this into
        the `for topic, payload, goal_id in ...` loop the other handlers
        use."""
        aborted = self.goal_id
        self.target = None
        self.goal_id = None
        self._reset_goal_scoped()
        return aborted

    def on_base_pose(self, pose: list[float], sim_time_ns: int | None) -> list:
        """Latest base pose (MOB-1 base_pose) with its sim stamp (TC-2);
        consumed by the next tick.

        None = no usable sim clock on this pose (issue #160 item 1, the
        BG-3 trust boundary extended to nav): geometry still updates —
        control keeps steering — but the machine's clock HOLDS, so the
        stall/timeout budgets freeze rather than anchor at 0 or jump on
        garbage.

        A blind machine cannot enforce its own stall or timeout budget, so
        it must not steer indefinitely: after BLIND_POSE_BUDGET consecutive
        unstamped poses the goal fails with `no_sim_clock` and the node
        zeroes the base (issue #182). An earlier comment here claimed the
        ADR-29 wall net covered this path; it did not — that net keys off
        command SILENCE, and this node commands on every pose, so it kept
        the net open forever. The guard now bounds blind driving directly
        (`base_blind_wall`, enforced on its COMMAND path so the stop
        actually sticks) and is the safety bound; this budget ends the GOAL
        with a diagnosis so nav stops steering a base the guard is holding
        at zero. It is the slower of the two on the store profile — see
        BLIND_POSE_BUDGET for the rtf arithmetic."""
        if self.target is not None:
            self.pose = [float(v) for v in pose]
            if sim_time_ns is None:
                self._blind_poses += 1
            else:
                self._sim_ns = int(sim_time_ns)
                self._blind_poses = 0
        return []

    def _finish(self, status: str, failure: str | None) -> list:
        elapsed = 0.0 if self._t0_ns is None else (self._sim_ns - self._t0_ns) / 1e9
        result = {"status": status, "failure": failure, "t_end": round(elapsed, 3)}
        goal_id = self.goal_id
        self.target = None
        self.goal_id = None
        return [("nav_result", result, goal_id)]

    def on_tick(self) -> list:
        """One lifecycle step; the CALLER is the clock — since ADR-29 the
        nav node calls this once per serviced base_pose (50 Hz sim), so
        `ticks` counts poses, while stall/timeout stay pure sim-time."""
        if self.target is None or self.pose is None:
            return []
        self.ticks += 1
        # budgets anchor at the first USABLE stamp (issue #160 item 1): a
        # goal whose poses are all unstamped keeps _t0_ns None and skips
        # the budget checks below entirely
        if self._t0_ns is None and self._sim_ns is not None:
            self._t0_ns = self._sim_ns
            self._progress_ns = self._sim_ns
        dist = math.hypot(self.target[0] - self.pose[0], self.target[1] - self.pose[1])
        yaw_err = abs(_wrap(self.target[2] - self.pose[2]))
        # hysteresis: latch rotate-only inside the radius; release only if
        # pushed well outside (2x), so boundary chatter cannot restart drive
        was_rotating = self.rotating
        if dist <= self.arrival_tol_m:
            self.rotating = True
        elif dist > 2 * self.arrival_tol_m:
            self.rotating = False
        if was_rotating != self.rotating:
            # phase change: reset the progress baselines
            self._best_dist = math.inf
            self._best_head = math.inf
            self._progress_ns = self._sim_ns
        # arrival requires BOTH translation AND orientation to converge
        # (MOB-2); once latched-rotating, the capture band counts as arrived
        # — rotate-only cannot translate, so demanding the tight radius from
        # a captured stall would spin forever and fail blocked
        arrived_dist = dist <= (self.capture_tol_m if self.rotating else self.arrival_tol_m)
        if arrived_dist and yaw_err <= self.arrival_yaw_rad:
            return self._finish("success", None)
        # blind budget is checked AFTER arrival (issue #182 review): geometry
        # is measured from base_pose and needs no clock, so a goal that has
        # actually reached its target has succeeded whether or not stamps
        # ever arrived — failing a COMPLETED goal would contradict
        # test_never_stamped_goal_still_controls_and_arrives. What must not
        # continue is blind STEERING, and that is what this bounds.
        if self._blind_poses >= BLIND_POSE_BUDGET:
            # fail closed rather than steer on a clock that never arrived:
            # the caller zeroes the base on any terminal nav_result, so this
            # both stops the motion and says why
            return self._finish("fail", "no_sim_clock")
        # progress tracking (MOB-2 blocked): PHASE-AWARE and three-way —
        # while latched-rotating, progress is the FINAL-yaw error; while
        # driving, progress is distance OR the heading-to-bearing error
        # (turning in place toward the bearing IS progress — T15 round 12:
        # a mutex-creeped turn read as blocked because dist stood still)
        progressed = False
        if self.rotating:
            if yaw_err < self._best_dist - 1e-6:
                self._best_dist = yaw_err
                progressed = True
        else:
            bearing = math.atan2(self.target[1] - self.pose[1], self.target[0] - self.pose[0])
            head_err = abs(_wrap(bearing - self.pose[2]))
            if dist < self._best_dist - 1e-6:
                self._best_dist = dist
                progressed = True
            if head_err < self._best_head - 1e-4:
                self._best_head = head_err
                progressed = True
        if progressed:
            self._progress_ns = self._sim_ns
        elif (
            self._sim_ns is not None
            and self._progress_ns is not None
            and (self._sim_ns - self._progress_ns) / 1e9 >= self.stall_s
        ):
            if not self.rotating and dist <= self.capture_tol_m:
                # captured: the drive stalled ON the target (within the
                # band) — hand off to the final rotate instead of blocked
                self.rotating = True
                self._best_dist = math.inf
                self._best_head = math.inf
                self._progress_ns = self._sim_ns
            else:
                return self._finish("fail", "blocked")
        if self._t0_ns is not None and (self._sim_ns - self._t0_ns) / 1e9 >= self.timeout_s:
            return self._finish("fail", "timeout")
        # MOB-2 contract feedback is {t, dist_remaining}; orientation progress
        # is tracked internally (above) and verified via base_pose, not
        # exposed as an unapproved contract field
        return [("nav_feedback", {"t": self.ticks, "dist_remaining": dist}, self.goal_id)]


# Consecutive unstamped poses a nav goal tolerates before failing closed
# (issue #182). Counted in POSES, not wall seconds, so the failure point is
# a function of the stream rather than host load (CON-5).
#
# 250 poses is 5 SIM seconds at the MOB-1 50 Hz cadence. Note SIM: the wall
# duration is 5/rtf, so ~10 s on the desk profile (rtf~0.5) and ~71 s on the
# store profile (rtf~0.07) that the mobile embodiment actually runs. An
# earlier revision of this comment claimed "~5 s ... far inside the guard's
# 10 s backstop, so nav fails first"; that silently mixed sim and wall and
# is false on every scene we measure — exactly the trap docs/glossary.md
# warns about under `rtf`. The guard's blind-drive stop fires FIRST on the
# store profile, and it is the safety bound; this budget is not a backstop
# and must not be read as one. It exists to END THE GOAL with a diagnosis
# (`no_sim_clock`) rather than leave nav steering at a stopped base, and it
# is deliberately generous so no transient gap can trip it.
BLIND_POSE_BUDGET = 250

# proportional gains for the diff-drive controller (MOB-2); dimensionless
# scaling of distance->v and heading-error->omega, clamped to the base
# limits (MOB-3). Turn-in-place when badly misaligned: v is scaled down by
# the heading alignment so the base does not arc wide through keep-out.
_K_V = 1.0
_K_OMEGA = 2.0


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def base_cmd_toward(
    pose,
    target,
    limits,
    arrival_tol_m: float = 0.05,
    rotate_only: bool = False,
    rotate_omega_max: float | None = None,
    near_field_m: float | None = None,
) -> tuple[float, float]:
    """Diff-drive base_cmd [v, omega] driving `pose` toward `target`
    (store frame), clamped to the base velocity limits (MOB-2/MOB-3).

    Two phases: while farther than `arrival_tol_m`, steer toward the target
    POSITION and drive forward; once in position (or the caller latches
    `rotate_only` — NavStateMachine.rotating's hysteresis), hold v=0 and
    rotate in place to the target YAW. Inside `near_field_m` the drive
    phase caps omega like the rotate phase (T15/PR #21 round 3): near the
    target the bearing swings fast, and a saturated turn with the pipeline
    loop delay ORBITS the target instead of entering the arrival radius."""
    dx = float(target[0]) - float(pose[0])
    dy = float(target[1]) - float(pose[1])
    dist = math.hypot(dx, dy)
    if not rotate_only and dist > arrival_tol_m:
        heading_err = _wrap(math.atan2(dy, dx) - float(pose[2]))
        omega_cap = limits.omega_max
        if near_field_m is not None and dist < near_field_m:
            omega_cap = min(omega_cap, rotate_omega_max or omega_cap)
        omega = max(-omega_cap, min(omega_cap, _K_OMEGA * heading_err))
        # only drive forward while roughly aligned; turn in place otherwise
        align = max(0.0, math.cos(heading_err))
        v = max(0.0, min(limits.v_max, _K_V * dist * align))
        return v, omega
    # in position: rotate to the target orientation, capped so the
    # loop-delay overshoot stays inside the arrival band (T15 round 8)
    cap = min(limits.omega_max, rotate_omega_max or limits.omega_max)
    yaw_err = _wrap(float(target[2]) - float(pose[2]))
    omega = max(-cap, min(cap, _K_OMEGA * yaw_err))
    return 0.0, omega
