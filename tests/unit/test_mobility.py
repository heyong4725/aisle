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

        return NavStateMachine(arrival_tol_m=0.1, timeout_ticks=20, stall_ticks=5)

    def test_goal_then_feedback_until_arrival(self):
        from aisle.mobility.nav import NavStateMachine

        m = NavStateMachine(arrival_tol_m=0.1, timeout_ticks=20, stall_ticks=5)
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
