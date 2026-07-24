"""Unit tests for the mobility contract's pure cores (SPEC 210) — no dora,
no sim (CON-12). Nav-goal location resolution (MOB-2) and the base/arm
mutual-exclusion clamp (MOB-3)."""

import pytest

pytestmark = pytest.mark.unit


class TestLocationResolver:
    def test_named_location_resolves_to_pose(self):
        """MOB-2: a nav_goal naming a known location resolves to its
        (x, y, yaw) from scenes/locations.toml."""
        from aisle.mobility.nav import load_locations, resolve_nav_goal

        locations = load_locations()
        pose = resolve_nav_goal({"location": "counter"}, locations)
        assert len(pose) == 3
        assert pose == pytest.approx(locations["counter"])

    def test_explicit_pose_passes_through(self):
        """MOB-2: a nav_goal carrying an explicit pose is used verbatim."""
        from aisle.mobility.nav import resolve_nav_goal

        pose = resolve_nav_goal({"pose": [1.0, 2.0, 0.5]}, {})
        assert pose == [1.0, 2.0, 0.5]

    def test_unknown_location_is_rejected(self):
        """MOB-2: an unknown named location is an explicit error, never a
        silent default."""
        from aisle.mobility.nav import resolve_nav_goal

        with pytest.raises(ValueError, match="unknown location"):
            resolve_nav_goal({"location": "moon"}, {"counter": [0.0, 0.0, 0.0]})

    def test_goal_without_location_or_pose_is_rejected(self):
        from aisle.mobility.nav import resolve_nav_goal

        with pytest.raises(ValueError, match="location.*pose"):
            resolve_nav_goal({}, {"counter": [0.0, 0.0, 0.0]})


class TestBaseArmExclusion:
    def _limits(self):
        from aisle.mobility.guard import load_base_limits

        return load_base_limits("mobile")

    def test_idle_arm_allows_full_base_speed(self):
        """MOB-3: with the arm idle, a base_cmd within the velocity limits
        passes through unchanged (no mutex)."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd([lim.v_max, 0.0], arm_in_motion=False, limits=lim)
        assert safe == pytest.approx([lim.v_max, 0.0])
        assert viols == []

    def test_arm_motion_clamps_base_to_creep(self):
        """MOB-3: arm motion and base motion above v_creep MUST NOT coexist
        — the base is clamped to v_creep and a base_arm_exclusion violation
        is emitted (clamp, never drop; BG-3)."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd([lim.v_max, lim.omega_max], arm_in_motion=True, limits=lim)
        assert abs(safe[0]) <= lim.v_creep + 1e-9
        assert abs(safe[1]) <= lim.omega_creep + 1e-9
        assert any(v["reason"] == "base_arm_exclusion" for v in viols)

    def test_arm_motion_keeps_a_creep_command(self):
        """MOB-3: a base command already at/below creep is legal even with
        the arm moving — no violation."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd([lim.v_creep, 0.0], arm_in_motion=True, limits=lim)
        assert safe == pytest.approx([lim.v_creep, 0.0])
        assert viols == []

    def test_base_velocity_limit_is_clamped(self):
        """MOB-3: a base_cmd exceeding v_max/omega_max is clamped to the
        limit with a base_velocity violation."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd(
            [lim.v_max * 3, -lim.omega_max * 3], arm_in_motion=False, limits=lim
        )
        assert safe == pytest.approx([lim.v_max, -lim.omega_max])
        assert any(v["reason"] == "base_velocity" for v in viols)

    def test_nan_command_holds_instead_of_maxing_out(self):
        """MOB-3/BG-3: a NaN base_cmd MUST fail safe to a hold — NOT slip
        through the clip as max velocity. A base_malformed violation is
        emitted and the requested value is JSON-safe (None, not NaN)."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd([float("nan"), 0.0], arm_in_motion=False, limits=lim)
        assert safe == [0.0, 0.0]
        assert any(v["reason"] == "base_malformed" for v in viols)
        assert viols[0]["requested"][0] is None  # JSON-safe, not NaN

    def test_short_command_holds_without_crashing(self):
        """MOB-3/BG-3: a too-short base_cmd MUST NOT IndexError-crash the
        safety node; it holds and reports base_malformed."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd([0.5], arm_in_motion=False, limits=lim)
        assert safe == [0.0, 0.0]
        assert any(v["reason"] == "base_malformed" for v in viols)


class TestKeepOut:
    """MOB-3 keep-out: with the arm extended, the base must not translate
    into a shelf's keep-out radius (min_shelf_dist_m)."""

    def _limits(self):
        from aisle.mobility.guard import load_base_limits

        return load_base_limits("mobile")

    def test_extended_arm_blocked_toward_near_shelf(self):
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        shelf = [(1.0, 0.0, 0.2, 0.5)]  # AABB just ahead
        # base at origin facing +x, 0.3 m from the shelf face (< 0.35 keep-out)
        safe, viols = clamp_base_cmd(
            [0.5, 0.0],
            arm_in_motion=False,
            limits=lim,
            base_pose=[0.5, 0.0, 0.0],
            shelves=shelf,
            arm_extended=True,
        )
        assert safe[0] == 0.0
        assert any(v["reason"] == "base_keepout" for v in viols)

    def test_backing_away_from_shelf_is_allowed(self):
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        shelf = [(1.0, 0.0, 0.2, 0.5)]
        # facing AWAY from the shelf (yaw=pi): forward motion recedes -> legal
        safe, viols = clamp_base_cmd(
            [0.5, 0.0],
            arm_in_motion=False,
            limits=lim,
            base_pose=[0.5, 0.0, 3.14159],
            shelves=shelf,
            arm_extended=True,
        )
        assert safe[0] == pytest.approx(0.5)
        assert not any(v["reason"] == "base_keepout" for v in viols)

    def test_retracted_arm_ignores_keepout(self):
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        shelf = [(1.0, 0.0, 0.2, 0.5)]
        safe, viols = clamp_base_cmd(
            [0.5, 0.0],
            arm_in_motion=False,
            limits=lim,
            base_pose=[0.5, 0.0, 0.0],
            shelves=shelf,
            arm_extended=False,
        )
        assert safe[0] == pytest.approx(0.5)
        assert not any(v["reason"] == "base_keepout" for v in viols)

    def test_velocity_capped_to_avoid_crossing_boundary(self):
        """Re-review #3: keep-out must prevent ENTRY, not just motion once
        inside. From just outside the zone, v is capped to the remaining
        clearance / dt so one step cannot cross the boundary."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()  # min_shelf_dist 0.35, base_cmd_dt_s 0.02
        # base at origin facing +x; shelf face 0.36 m ahead -> 0.01 m of legal
        # travel -> max_v = 0.01 / 0.02 = 0.5 m/s
        shelf = [(0.86, 0.0, 0.5, 0.5)]  # AABB face at 0.36
        safe, viols = clamp_base_cmd(
            [0.8, 0.0],
            arm_in_motion=False,
            limits=lim,
            base_pose=[0.0, 0.0, 0.0],
            shelves=shelf,
            arm_extended=True,
        )
        assert safe[0] == pytest.approx(0.5, abs=1e-6)  # capped, not 0, not 0.8
        assert any(v["reason"] == "base_keepout" for v in viols)

    def test_fails_closed_without_pose(self):
        """Re-review #2: with the arm reaching but no base_pose feedback the
        keep-out cannot be verified, so the base is held at 0 (fail closed)."""
        from aisle.mobility.guard import clamp_base_cmd

        lim = self._limits()
        safe, viols = clamp_base_cmd(
            [0.5, 0.0],
            arm_in_motion=False,
            limits=lim,
            base_pose=None,
            shelves=[(1.0, 0.0, 0.2, 0.5)],
            arm_extended=True,
        )
        assert safe[0] == 0.0
        assert any(v["reason"] == "base_keepout" for v in viols)

    def test_base_pose_validation(self):
        """Re-review #3: a base_pose is only usable if it is exactly three
        finite values; a short vector or a non-finite coordinate is rejected
        (the guard then caches None and keep-out fails closed)."""
        from aisle.mobility.guard import valid_base_pose

        assert valid_base_pose([0.0, 0.0, 0.0])
        assert not valid_base_pose([0.0, 0.0])  # short -> would IndexError
        assert not valid_base_pose([float("inf"), 0.0, 0.0])  # inf -> would bypass
        assert not valid_base_pose([0.0, 0.0, float("nan")])  # nan yaw
        # TOTAL over non-numeric payloads (BG-3 no-crash): must not raise
        assert not valid_base_pose([None, 0.0, 0.0])
        assert not valid_base_pose([["bad"], 0.0, 0.0])
        assert not valid_base_pose(["x", "y", "z"])


class TestArmMotionMutexWindow:
    """MOB-3 (PR #14 review): the mutex must represent ONGOING motion, not
    just whether the latest target differed. A repeated target while the arm
    still travels keeps the base clamped; command silence releases it."""

    _HOLD = 1.0

    def test_target_change_opens_the_window(self):
        from aisle.mobility.guard import base_creep_deadline

        deadline = base_creep_deadline(float("-inf"), True, now=10.0, hold_s=self._HOLD)
        assert 10.0 < deadline  # arm_in_motion True right after a move

    def test_repeated_target_keeps_the_window_open(self):
        """A repeated (unchanged) target does NOT reset the flag false while
        the arm is still inside the hold window opened by the last move."""
        from aisle.mobility.guard import base_creep_deadline

        deadline = base_creep_deadline(float("-inf"), True, now=0.0, hold_s=self._HOLD)
        # 0.5 s later the SAME target arrives (no change) — still in motion
        deadline = base_creep_deadline(deadline, target_changed=False, now=0.5, hold_s=self._HOLD)
        assert 0.5 < deadline  # 0.5 < 1.0: base stays clamped mid-travel

    def test_command_silence_expires_the_window(self):
        """After the hold elapses with no new arm command, the base is
        released — the flag is not stuck true forever."""
        from aisle.mobility.guard import base_creep_deadline

        deadline = base_creep_deadline(float("-inf"), True, now=0.0, hold_s=self._HOLD)
        assert not (1.5 > 0 and 1.5 < deadline)  # 1.5 s later: released


class TestMobileValidation:
    """MOB-4: the mobile profile's arm subtree is franka-identical, and
    base-requiring nodes need a base profile."""

    def _agnostic(self):
        return {"embodiment": {"arm": ["franka", "so101"], "gripper": "any"}}

    def test_franka_arm_node_validates_under_mobile(self):
        """A franka-arm capability validates unchanged under `mobile` —
        mobile resolves to the franka arm (MOB-4)."""
        from aisle.harness.validate import validate_nodes

        manifests = {"ik-trajectory": {"embodiment": {"arm": ["franka"], "gripper": "parallel"}}}
        nodes = [{"id": "ik-trajectory"}]
        errors, _ = validate_nodes(nodes, manifests, set(), "mobile", allow_unproven=True)
        assert not [e for e in errors if e["code"] == "EMBODIMENT_MISMATCH"]

    def test_base_node_requires_a_base_profile(self):
        """A base-requiring node validates under `mobile` but is an
        EMBODIMENT_MISMATCH on a fixed-base graph (franka) — MOB-4."""
        from aisle.harness.validate import validate_nodes

        manifests = {
            "nav-planner": {"embodiment": {"arm": ["franka", "so101"], "base": ["mobile"]}}
        }
        nodes = [{"id": "nav-planner"}]
        ok, _ = validate_nodes(nodes, manifests, set(), "mobile", allow_unproven=True)
        assert not [e for e in ok if e["code"] == "EMBODIMENT_MISMATCH"]
        bad, _ = validate_nodes(nodes, manifests, set(), "franka", allow_unproven=True)
        assert [e for e in bad if e["code"] == "EMBODIMENT_MISMATCH"]


def test_base_topic_schemas_in_vocabulary():
    """MOB-1: the mobile base topics carry typed Arrow schemas in the CAP-2
    vocabulary — base_pose Float32[3], base_cmd Float32[2], base_scan
    Float32[n] (planar ranges)."""
    from aisle.harness.registry import load_vocabulary
    from aisle.scenes.pharmacy import _REPO_ROOT

    vocab = load_vocabulary(_REPO_ROOT)
    assert vocab["base_pose3d_f32"] == {"arrow": "Float32", "shape": "3"}
    assert vocab["base_cmd2d_f32"] == {"arrow": "Float32", "shape": "2"}
    assert vocab["base_scan_f32"] == {"arrow": "Float32", "shape": "n_scan"}


class TestNavLifecycle:
    """MOB-2: the nav action's pure lifecycle — goal opens it, per-tick
    feedback {t, dist_remaining} >= 2 Hz, and a result {status, failure,
    t_end}. Deterministic ticks (CON-5), no wall clock."""

    def _machine(self):
        from aisle.mobility.nav import NavStateMachine

        return NavStateMachine(
            arrival_tol_m=0.1, timeout_ticks=20, stall_ticks=5, arrival_yaw_rad=0.1
        )

    def test_goal_then_feedback_until_arrival(self):
        from aisle.mobility.nav import NavStateMachine

        m = NavStateMachine(arrival_tol_m=0.1, timeout_ticks=20, stall_ticks=5, arrival_yaw_rad=0.1)
        assert m.on_goal([1.0, 0.0, 0.0], "nav-1") == []
        m.on_base_pose([0.0, 0.0, 0.0])
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"
        assert out[0][1]["t"] == 1 and out[0][1]["dist_remaining"] == pytest.approx(1.0)
        # drive closer, then arrive
        m.on_base_pose([0.95, 0.0, 0.0])
        out = m.on_tick()
        assert out[0][0] == "nav_result"
        assert out[0][1] == {"status": "success", "failure": None, "t_end": 2}

    def test_second_goal_while_active_is_refused(self):
        """TC-7: nav actions do not overlap."""
        m = self._machine()
        m.on_goal([1.0, 0.0, 0.0], "nav-1")
        assert m.on_goal([2.0, 0.0, 0.0], "nav-2") == []

    def test_timeout(self):
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([0.0, 0.0, 0.0])
        # never arrives, but keeps making tiny progress so it is not blocked
        result = None
        for i in range(1, 30):
            m.on_base_pose([i * 0.01, 0.0, 0.0])
            out = m.on_tick()
            if out and out[0][0] == "nav_result":
                result = out[0][1]
                break
        assert result == {"status": "fail", "failure": "timeout", "t_end": 20}

    def test_blocked_when_no_progress(self):
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([1.0, 0.0, 0.0])  # stuck here
        result = None
        for _ in range(10):
            out = m.on_tick()  # pose never changes
            if out and out[0][0] == "nav_result":
                result = out[0][1]
                break
        assert result is not None and result["failure"] == "blocked"

    def test_yaw_must_converge_before_success(self):
        """MOB-2 (PR #14 re-review): a pose goal is NOT complete on x/y alone
        — orientation must converge too. At the target position with the
        wrong yaw the action keeps running until the yaw is within tolerance."""
        from aisle.mobility.nav import NavStateMachine

        m = NavStateMachine(
            arrival_tol_m=0.1, timeout_ticks=50, stall_ticks=50, arrival_yaw_rad=0.1
        )
        m.on_goal([0.0, 0.0, 1.5708], "y1")
        m.on_base_pose([0.0, 0.0, 0.0])  # in position, wrong orientation
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"  # NOT success — yaw not converged
        assert set(out[0][1]) == {"t", "dist_remaining"}  # MOB-2 contract shape
        m.on_base_pose([0.0, 0.0, 1.55])  # rotated close to target yaw
        out = m.on_tick()
        assert out[0][0] == "nav_result" and out[0][1]["status"] == "success"


class TestBaseController:
    """MOB-2: the pure diff-drive controller that drives base_cmd toward
    the nav target, clamped to the base velocity limits (MOB-3)."""

    def _lim(self):
        from aisle.mobility.guard import load_base_limits

        return load_base_limits("mobile")

    def test_drives_forward_toward_aligned_target(self):
        from aisle.mobility.nav import base_cmd_toward

        v, omega = base_cmd_toward([0.0, 0.0, 0.0], [2.0, 0.0, 0.0], self._lim())
        assert v > 0 and abs(omega) < 1e-6  # straight ahead

    def test_turns_toward_offset_target(self):
        from aisle.mobility.nav import base_cmd_toward

        v, omega = base_cmd_toward([0.0, 0.0, 0.0], [0.0, 2.0, 0.0], self._lim())
        assert omega > 0  # target is to the left (+y) -> turn left

    def test_clamped_to_limits(self):
        from aisle.mobility.nav import base_cmd_toward

        lim = self._lim()
        v, omega = base_cmd_toward([0.0, 0.0, 0.0], [100.0, 0.0, 0.0], lim)
        assert 0 <= v <= lim.v_max and abs(omega) <= lim.omega_max

    def test_stops_at_target(self):
        from aisle.mobility.nav import base_cmd_toward

        v, omega = base_cmd_toward([1.0, 1.0, 0.0], [1.0, 1.0, 0.0], self._lim())
        assert v == pytest.approx(0.0) and omega == pytest.approx(0.0)

    def test_rotates_in_place_to_target_yaw(self):
        """MOB-2 (PR #14 re-review): at the target position but wrong yaw, the
        controller holds v=0 and rotates toward the target orientation."""
        from aisle.mobility.nav import base_cmd_toward

        v, omega = base_cmd_toward([1.0, 0.0, 0.0], [1.0, 0.0, 1.5708], self._lim())
        assert v == pytest.approx(0.0) and omega > 0


class TestKinematicBase:
    """MOB-1/MOB-5 (ADR-13): the kinematic unicycle base integrates
    base_cmd (v, omega) into a store-frame pose deterministically."""

    def test_straight_line_advances_along_heading(self):
        from aisle.mobility.base import integrate_base_pose

        pose = integrate_base_pose([0.0, 0.0, 0.0], [1.0, 0.0], dt=0.1)
        assert pose == pytest.approx([0.1, 0.0, 0.0])

    def test_advances_along_current_yaw(self):
        import math

        from aisle.mobility.base import integrate_base_pose

        pose = integrate_base_pose([0.0, 0.0, math.pi / 2], [1.0, 0.0], dt=0.1)
        assert pose[0] == pytest.approx(0.0, abs=1e-9)
        assert pose[1] == pytest.approx(0.1)

    def test_pure_rotation_holds_position(self):
        from aisle.mobility.base import integrate_base_pose

        pose = integrate_base_pose([1.0, 2.0, 0.0], [0.0, 1.0], dt=0.5)
        assert pose[0] == pytest.approx(1.0) and pose[1] == pytest.approx(2.0)
        assert pose[2] == pytest.approx(0.5)

    def test_yaw_wraps_to_pi_range(self):
        import math

        from aisle.mobility.base import integrate_base_pose

        pose = integrate_base_pose([0.0, 0.0, 3.0], [0.0, 1.0], dt=1.0)  # 3.0 + 1.0 = 4.0 -> wrap
        assert -math.pi <= pose[2] <= math.pi
        assert pose[2] == pytest.approx(4.0 - 2 * math.pi)

    def test_deterministic(self):
        from aisle.mobility.base import integrate_base_pose

        a = integrate_base_pose([0.0, 0.0, 0.3], [0.7, -0.4], dt=0.02)
        b = integrate_base_pose([0.0, 0.0, 0.3], [0.7, -0.4], dt=0.02)
        assert a == b


class TestBaseScan:
    """MOB-1: base_scan is a flat 2-D raycast (ADR-13) from the base origin
    against the scene's AABB obstacles, returning n ranges capped at
    range_max."""

    def test_ray_hits_obstacle_ahead(self):
        from aisle.mobility.base import base_scan_ranges

        # single obstacle 2 m ahead (+x); a forward-only 1-ray scan
        obstacles = [(2.0, 0.0, 0.5, 0.5)]  # cx, cy, hx, hy
        ranges = base_scan_ranges(
            [0.0, 0.0, 0.0], obstacles, n=1, angle_min=0.0, angle_max=0.0, range_max=5.0
        )
        assert len(ranges) == 1
        assert ranges[0] == pytest.approx(1.5, abs=1e-6)  # 2.0 - half 0.5

    def test_clear_ray_returns_range_max(self):
        from aisle.mobility.base import base_scan_ranges

        ranges = base_scan_ranges(
            [0.0, 0.0, 0.0], [], n=1, angle_min=0.0, angle_max=0.0, range_max=5.0
        )
        assert ranges[0] == pytest.approx(5.0)

    def test_scan_count_and_range_cap(self):
        import math

        from aisle.mobility.base import base_scan_ranges

        obstacles = [(1.0, 0.0, 0.1, 0.1)]
        ranges = base_scan_ranges(
            [0.0, 0.0, 0.0], obstacles, n=8, angle_min=-math.pi, angle_max=math.pi, range_max=3.0
        )
        assert len(ranges) == 8
        assert all(0 <= r <= 3.0 for r in ranges)

    def test_base_yaw_rotates_the_scan(self):
        import math

        from aisle.mobility.base import base_scan_ranges

        # obstacle to the +y side; facing +y (yaw=pi/2) the forward ray hits it
        obstacles = [(0.0, 2.0, 0.5, 0.5)]
        ranges = base_scan_ranges(
            [0.0, 0.0, math.pi / 2], obstacles, n=1, angle_min=0.0, angle_max=0.0, range_max=5.0
        )
        assert ranges[0] == pytest.approx(1.5, abs=1e-6)


class TestRotateOnlyLatch:
    """T15 round 5: drive/rotate alternation at the arrival boundary
    chattered forever and read as blocked — once inside the radius the
    machine latches rotate-only, released only well outside (hysteresis)."""

    def _machine(self):
        from aisle.mobility.nav import NavStateMachine

        return NavStateMachine(
            arrival_tol_m=0.05, timeout_ticks=100, stall_ticks=50, arrival_yaw_rad=0.05
        )

    def test_latch_engages_inside_and_holds_at_boundary(self):
        m = self._machine()
        m.on_goal([1.0, 0.0, 1.5708], "g")
        m.on_base_pose([0.96, 0.0, 0.0])  # inside the radius
        m.on_tick()
        assert m.rotating
        m.on_base_pose([0.93, 0.0, 0.5])  # drifted just past tol (0.07 < 2x)
        m.on_tick()
        assert m.rotating  # hysteresis holds
        m.on_base_pose([0.80, 0.0, 0.5])  # pushed well outside (0.2 > 2x)
        m.on_tick()
        assert not m.rotating

    def test_rotate_only_command_never_translates(self):
        from aisle.mobility.guard import load_base_limits
        from aisle.mobility.nav import base_cmd_toward

        lim = load_base_limits("mobile")
        v, omega = base_cmd_toward(
            [0.93, 0.0, 0.0], [1.0, 0.0, 1.5708], lim, 0.05, rotate_only=True
        )
        assert v == 0.0 and omega > 0

    def test_latched_rotation_converges_in_lifecycle(self):
        """With the latch, a goal at the radius boundary converges to
        success instead of stalling blocked."""
        from aisle.mobility.base import integrate_base_pose
        from aisle.mobility.guard import load_base_limits
        from aisle.mobility.nav import base_cmd_toward

        lim = load_base_limits("mobile")
        m = self._machine()
        m.on_goal([1.0, 0.0, 1.5708], "g")
        pose = [0.955, 0.0, 0.0]  # right at the boundary, wrong yaw
        result = None
        for _ in range(100):
            m.on_base_pose(pose)
            out = m.on_tick()
            if out and out[0][0] == "nav_result":
                result = out[0][1]
                break
            v, omega = base_cmd_toward(pose, m.target, lim, 0.05, rotate_only=m.rotating)
            pose = integrate_base_pose(pose, [v, omega], 0.02)
        assert result is not None and result["status"] == "success", result


def test_turn_in_place_toward_bearing_is_progress():
    """T15 round 12: a mutex-creeped turn-in-place toward the bearing must
    register as nav progress — the drive-phase metric counts heading
    improvement, not just distance."""
    from aisle.mobility.nav import NavStateMachine

    m = NavStateMachine(
        arrival_tol_m=0.05, timeout_ticks=1000, stall_ticks=50, arrival_yaw_rad=0.05
    )
    m.on_goal([1.0, 0.0, 0.0], "g")
    yaw = 3.0  # facing away; distance will not change while turning
    result = None
    for _ in range(300):
        m.on_base_pose([0.0, 0.0, yaw])
        out = m.on_tick()
        if out and out[0][0] == "nav_result":
            result = out[0][1]
            break
        yaw -= 0.01  # slow creep-rate turn toward the bearing (0)
    # 300 ticks of pure turning: NOT blocked (progress via heading)
    assert result is None or result["failure"] != "blocked", result


class TestNavCaptureBand:
    """MOB-2 capture band (T15/PR #21 round 3): a diff-drive base cannot
    point-stabilize onto a target it is effectively ON — the S1 gate run
    stalled 0.5 mm outside the arrival radius with yaw still ~pi off,
    dithered below the progress epsilons, and failed blocked three times."""

    def _machine(self):
        from aisle.mobility.nav import NavStateMachine

        return NavStateMachine(
            arrival_tol_m=0.05,
            timeout_ticks=2000,
            stall_ticks=5,
            arrival_yaw_rad=0.05,
            capture_tol_m=0.075,
        )

    def test_drive_stall_inside_capture_hands_off_to_rotate_then_succeeds(self):
        # the S1 gate failure verbatim: parked 0.0505 m out, yaw ~pi off
        m = self._machine()
        m.on_goal([-0.5, 0.0, 3.14], "nav-1")
        m.on_base_pose([-0.4995, -0.0505, -0.02])  # dist ~0.0505, stuck
        for _ in range(6):  # exhaust the drive-phase stall window
            out = m.on_tick()
            assert not (out and out[0][0] == "nav_result"), out
        assert m.rotating  # captured: final-rotate, not blocked
        m.on_base_pose([-0.4995, -0.0505, 3.13])  # rotated to the final yaw
        out = m.on_tick()
        assert out[0][0] == "nav_result" and out[0][1]["status"] == "success"

    def test_drive_stall_outside_capture_still_fails_blocked(self):
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([1.0, 0.0, 0.0])  # 4 m away, genuinely stuck
        result = None
        for _ in range(10):
            out = m.on_tick()
            if out and out[0][0] == "nav_result":
                result = out[0][1]
                break
        assert result is not None and result["failure"] == "blocked"

    def test_capture_band_never_relaxes_a_live_drive(self):
        # inside capture but still PROGRESSING: no early success, no
        # rotate handoff — the tight radius stays the aim point
        m = self._machine()
        m.on_goal([0.1, 0.0, 0.0], "nav-1")
        m.on_base_pose([0.04, 0.0, 0.0])  # dist 0.06: in capture, driving
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"
        assert not m.rotating

    def test_capture_tol_defaults_to_1p5x_arrival(self):
        from aisle.mobility.nav import NavStateMachine

        m = NavStateMachine(arrival_tol_m=0.1, timeout_ticks=20, stall_ticks=5, arrival_yaw_rad=0.1)
        assert m.capture_tol_m == pytest.approx(0.15)

    def test_load_nav_params_exposes_capture_tol(self):
        """The config value rides load_nav_params so the expert's verify
        gate and the IK envelope sweep read the SAME band nav enforces."""
        from aisle.mobility.nav import load_nav_params

        params = load_nav_params("mobile")
        assert params["capture_tol_m"] >= params["arrival_tol_m"]


class TestNavNearField:
    """MOB-2 near-field omega cap (T15/PR #21 round 3): near the target the
    bearing swings fast and a saturated turn with the pipeline loop delay
    ORBITS the target — the S1 gate run circled the counter for ~8 sim
    seconds (dist 0.19 -> 0.27) and failed blocked. Inside nav_near_field_m
    the drive phase turns at the rotate-phase cap."""

    def _limits(self):
        from aisle.mobility.guard import load_base_limits

        return load_base_limits("mobile")

    def test_near_target_drive_omega_is_capped(self):
        from aisle.mobility.nav import base_cmd_toward

        # beside the target (dist 0.2, bearing ~90 deg off): omega would
        # saturate at omega_max without the near-field cap
        v, omega = base_cmd_toward(
            [0.0, 0.0, 0.0],
            [0.0, 0.2, 0.0],
            self._limits(),
            arrival_tol_m=0.05,
            rotate_omega_max=0.3,
            near_field_m=0.25,
        )
        assert abs(omega) <= 0.3
        assert v <= 0.2 + 1e-9  # v stays dist-scaled

    def test_far_field_turn_rate_is_unchanged(self):
        from aisle.mobility.nav import base_cmd_toward

        limits = self._limits()
        v, omega = base_cmd_toward(
            [0.0, 0.0, 0.0],
            [0.0, 2.0, 0.0],
            limits,
            arrival_tol_m=0.05,
            rotate_omega_max=0.3,
            near_field_m=0.25,
        )
        assert abs(omega) == pytest.approx(limits.omega_max)

    def test_load_near_field_reads_config(self):
        from aisle.mobility.nav import load_near_field_m

        assert load_near_field_m("mobile") > 0.0
