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
