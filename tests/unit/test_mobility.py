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


class TestBaseWatchdogReason:
    """MOB-3 watchdog verdict (CON-5, ADR-29): sim-time staleness is the
    primary, deterministic check — it bounds the runaway TRAJECTORY a
    latched command can drive identically at every host rtf, where the old
    wall-clock window did not. The wall net only catches what the sim clock
    cannot see, and the BG-2 episode timeout wins over both."""

    def _reason(self, **kw):
        from aisle.mobility.guard import base_watchdog_reason, load_base_limits

        defaults = dict(
            episode_timed_out=False,
            last_cmd_sim_ns=0,
            now_sim_ns=0,
            last_cmd_wall_t=100.0,
            now_wall=100.0,
            sim_clock_blind=False,
            blind_since_wall=None,
            limits=load_base_limits("mobile"),
        )
        return base_watchdog_reason(**{**defaults, **kw})

    def test_fresh_command_is_left_alone(self):
        assert self._reason(now_sim_ns=int(0.5e9)) is None  # exactly at the window

    def test_helpers_are_importable_by_the_node(self):
        """CON-12: the guard node imports these by name inside main(); a
        rename here must fail a unit test, not a live dataflow."""
        from aisle.mobility import guard

        for name in ("sim_clock_is_blind", "blind_onset", "base_blind_drive"):
            assert callable(getattr(guard, name)), name

    def test_command_goes_stale_past_the_sim_window(self):
        assert self._reason(now_sim_ns=int(0.5e9) + 1) == "base_stale"

    def test_pre_pose_command_is_not_sim_stale(self):
        """A command with no sim reference (unstamped source, or latched
        before the first pose) must not fail open OR spuriously trip: the
        sim check is skipped, and only the wall net can stop it."""
        assert self._reason(last_cmd_sim_ns=None, now_sim_ns=10**12) is None

    def test_wall_net_catches_what_the_sim_clock_cannot_see(self):
        assert self._reason(
            last_cmd_sim_ns=None, now_sim_ns=None, now_wall=111.0, sim_clock_blind=True
        ) == ("base_stale_wall")

    def test_wall_net_needs_a_blind_sim_clock(self):
        """PR #156 review: a healthy-but-SLOW sim (valid stamps still
        flowing, however rarely) must never trip the wall net — while the
        sim clock works, the sim-time check owns the verdict at any rtf."""
        assert self._reason(now_wall=200.0, sim_clock_blind=False) is None

    def test_wall_net_sits_far_above_any_healthy_command_gap(self):
        # 2 s wall since the last cmd = a healthy gap even at rtf 0.01
        assert self._reason(now_wall=102.0, sim_clock_blind=True, blind_since_wall=100.0) is None

    def test_blind_drive_stops_a_base_whose_producer_keeps_commanding(self):
        """MOB-3, ADR-29 (issue #182): the command-silence net cannot fire
        when the producer emits on every pose, and the sim check cannot
        advance without a clock — so a base drove blind until the episode
        budget, up to 30 wall minutes. The blind-drive net measures from the
        clock going blind instead, so a fresh command does not hold it
        open."""
        # commands are FRESH the whole time (now_wall == last_cmd_wall_t)
        assert self._reason(
            last_cmd_sim_ns=None,
            now_sim_ns=None,
            last_cmd_wall_t=200.0,
            now_wall=200.0,
            sim_clock_blind=True,
            blind_since_wall=100.0,
        ) == ("base_blind_wall")

    def test_blind_drive_respects_the_same_backstop(self):
        """MOB-3: it must not fire early — a brief blind gap under the
        backstop is the transient the sim-time check is allowed to ride
        out."""
        assert (
            self._reason(
                last_cmd_sim_ns=None,
                now_sim_ns=None,
                last_cmd_wall_t=105.0,
                now_wall=105.0,
                sim_clock_blind=True,
                blind_since_wall=100.0,
            )
            is None
        )

    def test_blind_drive_window_is_exactly_the_configured_backstop(self):
        """MOB-3, CON-5: the window IS `base_wall_backstop_s` from env/
        limits.toml, not a number that happens to sit between the two cases
        above. Pins both the boundary (exactly at the window is still legal,
        mirroring test_fresh_command_is_left_alone) and the coupling to
        config — without this, hard-coding any threshold in (5, 100] passes."""
        from aisle.mobility.guard import load_base_limits

        backstop = load_base_limits("mobile").base_wall_backstop_s
        blind_at = 100.0
        common = dict(last_cmd_sim_ns=None, now_sim_ns=None, sim_clock_blind=True)
        # exactly at the window: not yet
        assert (
            self._reason(
                **common,
                last_cmd_wall_t=blind_at + backstop,
                now_wall=blind_at + backstop,
                blind_since_wall=blind_at,
            )
            is None
        )
        # one epsilon past it: fires
        assert (
            self._reason(
                **common,
                last_cmd_wall_t=blind_at + backstop + 1e-6,
                now_wall=blind_at + backstop + 1e-6,
                blind_since_wall=blind_at,
            )
            == "base_blind_wall"
        )

    def test_command_silence_is_reported_before_blind_drive(self):
        """MOB-3, ADR-29: the docstring's precedence is a contract, not an
        accident of layout. When BOTH wall windows are blown, the reported
        reason is `base_stale_wall` — the last command predates the clock
        going blind, so command-silence is the earlier and more specific
        diagnosis. Swapping the two branches must go red."""
        assert (
            self._reason(
                last_cmd_sim_ns=None,
                now_sim_ns=None,
                last_cmd_wall_t=100.0,  # silent since 100
                blind_since_wall=150.0,  # blind only since 150
                now_wall=200.0,  # both windows (10 s) are blown
                sim_clock_blind=True,
            )
            == "base_stale_wall"
        )

    def test_blind_drive_needs_a_blind_clock_too(self):
        """MOB-3: a stale onset left over from an earlier gap must not stop
        a base whose stamps have returned — the node clears the onset, and
        the verdict double-checks it."""
        assert self._reason(now_wall=200.0, sim_clock_blind=False, blind_since_wall=100.0) is None

    def test_sim_stale_wins_over_the_wall_net(self):
        """ADR-29 precedence: when both windows are blown, the deterministic
        sim verdict is reported, not the ops-alarm wall reason."""
        assert self._reason(
            now_sim_ns=int(0.5e9) + 1,
            now_wall=200.0,
            sim_clock_blind=True,
            blind_since_wall=100.0,
        ) == ("base_stale")

    def test_episode_timeout_wins_over_staleness(self):
        assert self._reason(episode_timed_out=True, now_sim_ns=10**12) == "base_timeout"


class TestBlindClockBookkeeping:
    """MOB-3, ADR-29, CON-5 (issue #182 review). The blind ONSET latch is the
    half of the #182 fix that decides whether the net can ever fire, and it
    originally lived inside the guard node's event loop where no unit test
    could reach it — deleting it outright left the whole suite green. It is
    pure now, and these are the tests that were impossible before."""

    def _limits(self):
        from aisle.mobility.guard import load_base_limits

        return load_base_limits("mobile")

    def test_no_pose_stamp_yet_reads_as_blind(self):
        """An env that has never produced a stamped pose has no clock: the
        wall net must arm rather than wait for a reference that never
        comes."""
        from aisle.mobility.guard import sim_clock_is_blind

        assert sim_clock_is_blind(
            base_pose_sim_ns=None, last_pose_wall_t=None, now_wall=0.0, limits=self._limits()
        )

    def test_flowing_stamps_are_never_blind_at_any_rtf(self):
        """PR #156's invariant, restated on the extracted helper: a healthy
        but very slow sim must be structurally unable to trip the net."""
        from aisle.mobility.guard import sim_clock_is_blind

        assert not sim_clock_is_blind(
            base_pose_sim_ns=1,
            last_pose_wall_t=1000.0,
            now_wall=1000.0,
            limits=self._limits(),
        )

    def test_a_silent_pose_stream_goes_blind_past_the_backstop(self):
        from aisle.mobility.guard import sim_clock_is_blind

        lim = self._limits()
        assert sim_clock_is_blind(
            base_pose_sim_ns=1,
            last_pose_wall_t=1000.0,
            now_wall=1000.0 + lim.base_wall_backstop_s + 1e-6,
            limits=lim,
        )

    def test_onset_latches_on_entry_and_then_holds(self):
        """The window must AGE. If the onset were re-stamped on every blind
        call, `now - onset` would stay ~0 and the net could never fire — the
        exact mutation (`elif not blind` -> `else`) that shipped green."""
        from aisle.mobility.guard import blind_onset

        first = blind_onset(None, sim_clock_blind=True, now_wall=100.0)
        assert first == 100.0
        assert blind_onset(first, sim_clock_blind=True, now_wall=100.5) == 100.0
        assert blind_onset(first, sim_clock_blind=True, now_wall=999.0) == 100.0

    def test_onset_clears_the_moment_stamps_return(self):
        """So a transient gap cannot accumulate across healthy stretches."""
        from aisle.mobility.guard import blind_onset

        assert blind_onset(100.0, sim_clock_blind=False, now_wall=101.0) is None

    def test_a_returning_clock_restarts_the_whole_window(self):
        """Two sub-backstop gaps separated by one good stamp must not add up
        into a stop: the second gap starts its own window."""
        from aisle.mobility.guard import base_blind_drive, blind_onset

        lim = self._limits()
        half = lim.base_wall_backstop_s * 0.6
        onset = blind_onset(None, sim_clock_blind=True, now_wall=0.0)
        onset = blind_onset(onset, sim_clock_blind=True, now_wall=half)
        onset = blind_onset(onset, sim_clock_blind=False, now_wall=half)  # stamp returns
        onset = blind_onset(onset, sim_clock_blind=True, now_wall=half)  # blind again
        assert not base_blind_drive(blind_since_wall=onset, now_wall=2 * half, limits=lim), (
            "two sub-backstop gaps summed into a stop"
        )

    def test_every_field_the_verdict_reads_is_re_armed_at_the_boundary(self):
        """MOB-3, CON-5 (issue #182 review). The guard node's per-env state
        must not carry an episode's watchdog windows into the next one, or
        the outcome depends on the episode INDEX under a fixed seed.

        This is the test the original omission could not have failed: the
        reset was an inline list of assignments inside the node's event
        loop. Adding a state key the verdict reads and forgetting the reset
        now fails HERE — the check is derived from
        base_watchdog_reason's own signature, not from a hand-copied list."""
        import inspect

        from aisle.mobility.guard import (
            BASE_WATCHDOG_EPISODE_RESET,
            base_watchdog_reason,
            reset_base_watchdog,
        )

        # every episode-scoped input the verdict reads, by name. now_wall /
        # now_sim_ns are readings, not state; limits is config; and
        # episode_timed_out is the EpisodeTimer's own business.
        params = set(inspect.signature(base_watchdog_reason).parameters)
        readings = {"now_wall", "now_sim_ns", "limits", "episode_timed_out", "sim_clock_blind"}
        state_inputs = params - readings
        # map verdict parameter -> guard state key
        state_key = {
            "last_cmd_sim_ns": "last_base_cmd_sim_ns",
            "last_cmd_wall_t": "last_base_cmd_wall_t",
            "blind_since_wall": "blind_since_wall",
        }
        missing = {p for p in state_inputs if state_key.get(p) not in BASE_WATCHDOG_EPISODE_RESET}
        assert not missing, (
            f"base_watchdog_reason reads {sorted(missing)} but the episode reset does not re-arm "
            "it; add it to BASE_WATCHDOG_EPISODE_RESET (or to `readings` if it is not state)"
        )

        # and the reset actually clears a dirtied state dict
        dirty = {k: "DIRTY" for k in BASE_WATCHDOG_EPISODE_RESET}
        dirty["base_pose_sim_ns"] = 12345  # deliberately NOT reset (ADR-29)
        reset_base_watchdog(dirty)
        assert dirty["blind_since_wall"] is None
        assert dirty["last_base_safe"] == [0.0, 0.0]
        assert dirty["base_pose_sim_ns"] == 12345, "the sim clock is monotonic across episodes"

    def test_the_reset_hands_out_distinct_mutable_values(self):
        """CON-5/BRG-5: `last_base_safe` is a list the node mutates per env.
        A shared module-level instance would let one env's reset alias
        another's — the fleet class of bug that dropped a box at a
        neighbour's reset moment."""
        from aisle.mobility.guard import reset_base_watchdog

        a, b = {}, {}
        reset_base_watchdog(a)
        reset_base_watchdog(b)
        assert a["last_base_safe"] == b["last_base_safe"]
        assert a["last_base_safe"] is not b["last_base_safe"]

    def test_blind_drive_is_false_without_an_onset(self):
        """BG-3: a caller that has never seen a blind moment must not crash
        or fire — the guard node's event loop is not allowed to raise."""
        from aisle.mobility.guard import base_blind_drive

        assert not base_blind_drive(blind_since_wall=None, now_wall=10**6, limits=self._limits())


class TestGuardMetadataParsing:
    """BG-3 (PR #156 review): metadata from upstream nodes is a trust
    boundary — a malformed env_id or sim stamp must degrade, never crash
    the guard's event loop (the whole safety gate dies with it)."""

    def test_env_id_total_over_garbage(self):
        from aisle.mobility.guard import parse_env_id

        assert parse_env_id({"env_id": 3}) == 3
        assert parse_env_id({}) == 0
        for bad in (None, "abc", [1], {"x": 1}, float("nan")):
            assert parse_env_id({"env_id": bad}) == 0

    def test_sim_stamp_total_over_garbage(self):
        from aisle.mobility.guard import parse_sim_stamp

        assert parse_sim_stamp({"sim_time_ns": 10_000_000}) == 10_000_000
        for blind in ({}, {"sim_time_ns": 0}, {"sim_time_ns": None}, {"sim_time_ns": "abc"}):
            assert parse_sim_stamp(blind) is None

    def test_zero_stamp_means_no_clock(self):
        """topics.stamp() defaults missing stamps to 0, so 0 must read as
        'no sim clock' — anchoring staleness at 0 against the monotonic
        run-long sim clock would falsely stale-stop the next command."""
        from aisle.mobility.guard import parse_sim_stamp

        assert parse_sim_stamp({"sim_time_ns": 0}) is None


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


class TestEpisodeBoundary:
    """MOB-2, TC-7, CON-5 (issue #179). `waypoint-nav` was the only stateful
    node in the retail graphs with no `reset_done` input, so a nav leg still
    in flight when an episode ended survived the boundary: the next
    episode's first goal was refused as "nav active", and the carried leg's
    nav_result then completed the NEW episode's subtask — invisible in the
    records, because the episode looks ordinary."""

    STEP_NS = 20_000_000

    def _machine(self):
        from aisle.mobility.nav import NavStateMachine

        return NavStateMachine(arrival_tol_m=0.1, timeout_s=10.0, stall_s=2.0, arrival_yaw_rad=0.1)

    def test_an_in_flight_goal_is_abandoned_and_the_next_one_is_accepted(self):
        """The headline failure: without the boundary the next episode's
        first nav_goal is silently dropped by the TC-7 non-overlap guard."""
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-001")
        m.on_base_pose([0.0, 0.0, 0.0], self.STEP_NS)
        m.on_tick()
        assert m.target is not None  # mid-leg when the episode ends

        assert m.on_episode_boundary() == "nav-001"  # reports what it dropped
        assert m.target is None and m.goal_id is None

        # the NEW episode's first goal is accepted, not refused
        m.on_goal([1.0, 0.0, 0.0], "nav-002")
        assert m.goal_id == "nav-002"

    def test_the_boundary_emits_nothing(self):
        """A nav_result here would carry the OLD goal_id into a fresh
        episode — the very confusion being fixed — and "the episode ended"
        is not one of MOB-2's failure values. Returning the goal_id rather
        than an emissions list also makes it impossible to feed into the
        `for topic, payload, goal_id in ...` loop the other handlers use."""
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-001")
        out = m.on_episode_boundary()
        assert isinstance(out, str)  # NOT a list of emissions

    def test_an_idle_boundary_is_a_no_op(self):
        """Resets arrive every episode, most with no leg in flight; the
        caller keys its log and its base-stop off a None return."""
        m = self._machine()
        assert m.on_episode_boundary() is None

    def test_the_boundary_clears_every_goal_scoped_field(self):
        """CON-5: the carried clock is the third consequence of #179 — PR
        #178 made the clock goal-scoped, but that reset sits behind the
        non-overlap early return, so the episode path never reached it.

        Derived from `_GOAL_SCOPED` rather than a hand-copied list, so a
        field added to the machine and forgotten in one of the three reset
        paths fails HERE."""
        from aisle.mobility.nav import NavStateMachine

        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-001")
        # dirty every goal-scoped field with a value that is not its fresh one
        for name, fresh in NavStateMachine._GOAL_SCOPED:
            setattr(m, name, "DIRTY" if fresh is None else None)
        m.on_episode_boundary()
        for name, fresh in NavStateMachine._GOAL_SCOPED:
            assert getattr(m, name) == fresh, f"{name} survived the episode boundary"

    def test_goal_scoped_defaults_are_immutable(self):
        """`_reset_goal_scoped` hands every instance the SAME object out of a
        class-level tuple, so a mutable default (`[]`, `{}`, `set()`) would
        be shared across machines and across episodes — one goal's writes
        leaking into the next, which is the bug class this list exists to
        prevent. Cheap to add, impossible to notice by reading."""
        from aisle.mobility.nav import NavStateMachine

        for name, fresh in NavStateMachine._GOAL_SCOPED:
            assert isinstance(fresh, (type(None), bool, int, float, str, tuple)), (
                f"{name}'s default {fresh!r} is mutable and would be SHARED by every "
                "NavStateMachine; store a factory or rebuild it per reset instead"
            )

    def test_every_graph_running_nav_wires_the_boundary_to_it(self):
        """The machine method is useless if nav never hears about the
        boundary, and "nobody wired the input" IS issue #179 — the pure
        tests above all pass on the broken graph. Checks the real graphs,
        not a fixture, and covers every graph that runs the node so the
        fix cannot land on expert_s1 alone (the other three had the
        identical hole)."""
        from pathlib import Path

        import yaml

        root = Path(__file__).resolve().parents[2]
        graphs = sorted(
            p for p in (root / "graphs").glob("*.yaml") if "nav_action.py" in p.read_text()
        )
        assert graphs, "no graph runs nav_action.py; this test has lost its subject"
        for path in graphs:
            nodes = yaml.safe_load(path.read_text())["nodes"]
            nav = [n for n in nodes if "nav_action.py" in str(n.get("path", ""))]
            assert nav, path.name
            for node in nav:
                inputs = node.get("inputs") or {}
                assert "reset_done" in inputs, (
                    f"{path.name}: {node['id']} has no reset_done input — an in-flight "
                    "nav leg will survive the episode boundary (issue #179)"
                )

    def test_the_manifests_declare_the_boundary_input(self):
        """CAP-1/VAL: the graph edge only validates if the manifest declares
        the port, and TWO manifests carry nav_action.py — the concrete
        `nav-action` node and the swappable `waypoint-nav` capability. They
        describe the same source, so they must not drift."""
        from pathlib import Path

        import yaml

        root = Path(__file__).resolve().parents[2]
        carrying = [
            (p, m)
            for p in sorted((root / "registry" / "manifests").glob("*.yaml"))
            if isinstance(m := yaml.safe_load(p.read_text()), dict)
            and m.get("source") == "src/aisle/nodes/nav_action.py"
        ]
        assert len(carrying) >= 2, [p.name for p, _ in carrying]
        for path, manifest in carrying:
            assert "reset_done" in (manifest.get("inputs") or {}), path.name

    def test_a_new_goal_and_the_boundary_agree_on_what_is_goal_scoped(self):
        """The two reset paths must not drift: whatever `on_goal` freshens,
        the boundary must freshen too (they share `_reset_goal_scoped`)."""
        from aisle.mobility.nav import NavStateMachine

        after_goal, after_boundary = self._machine(), self._machine()
        after_goal.on_goal([1.0, 0.0, 0.0], "nav-001")
        after_boundary.on_goal([1.0, 0.0, 0.0], "nav-001")
        after_boundary.on_base_pose([0.5, 0.0, 0.0], self.STEP_NS)
        after_boundary.on_tick()
        after_boundary.on_episode_boundary()
        for name, _ in NavStateMachine._GOAL_SCOPED:
            assert getattr(after_goal, name) == getattr(after_boundary, name), name


class TestNavResultRouting:
    """TC-7 (issue #179): a nav_result completes the leg it names, or
    nothing. Shared by s1-expert and both driver skills."""

    def test_the_matching_goal_id_is_accepted(self):
        from aisle.mobility.nav import nav_result_is_current

        assert nav_result_is_current("nav-007", "nav-007")

    def test_a_foreign_goal_id_is_rejected(self):
        """The #179 core: a leg carried over from a PREVIOUS episode emits
        its result into the next one. `nav_seq` is monotonic for the life of
        the process, so the ids genuinely discriminate."""
        from aisle.mobility.nav import nav_result_is_current

        assert not nav_result_is_current("nav-009", "nav-004")

    @pytest.mark.parametrize(
        ("expected", "reply"),
        [
            (None, "nav-001"),  # nothing pending an id
            ("nav-001", None),  # reply carried no goal_id
            (None, None),
            ("", ""),  # nav_action's empty-id base_cmd convention
            ("nav-001", 1),  # non-string metadata from the wire
            (1, "nav-001"),
        ],
    )
    def test_anything_unusable_fails_closed(self, expected, reply):
        """BG-3-style trust boundary: "cannot tell" must not complete a
        subtask. An empty id in particular is what nav_action stamps on its
        terminal zero base_cmd, and must never match a pending leg."""
        from aisle.mobility.nav import nav_result_is_current

        assert not nav_result_is_current(expected, reply)


class TestNavLifecycle:
    """MOB-2: the nav action's pure lifecycle — goal opens it, per-tick
    feedback {t, dist_remaining} >= 2 Hz, and a result {status, failure,
    t_end}. Timeout/stall are SIM-second budgets keyed to base_pose sim
    stamps (PR #21 round 4, CON-5): outcomes are a function of the
    trajectory alone, never of the host machine's rtf."""

    # 50 Hz base_pose: one sim stamp every 20 ms
    STEP_NS = 20_000_000

    def _machine(self):
        from aisle.mobility.nav import NavStateMachine

        return NavStateMachine(arrival_tol_m=0.1, timeout_s=0.4, stall_s=0.1, arrival_yaw_rad=0.1)

    def test_goal_then_feedback_until_arrival(self):
        m = self._machine()
        assert m.on_goal([1.0, 0.0, 0.0], "nav-1") == []
        m.on_base_pose([0.0, 0.0, 0.0], 0)
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"
        assert out[0][1]["t"] == 1 and out[0][1]["dist_remaining"] == pytest.approx(1.0)
        # drive closer, then arrive
        m.on_base_pose([0.95, 0.0, 0.0], self.STEP_NS)
        out = m.on_tick()
        assert out[0][0] == "nav_result"
        # t_end reports SIM seconds since the first stamped pose
        assert out[0][1] == {"status": "success", "failure": None, "t_end": 0.02}

    def test_second_goal_while_active_is_refused(self):
        """TC-7: nav actions do not overlap."""
        m = self._machine()
        m.on_goal([1.0, 0.0, 0.0], "nav-1")
        assert m.on_goal([2.0, 0.0, 0.0], "nav-2") == []

    def test_timeout_is_sim_time(self):
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        # never arrives, but keeps making tiny progress so it is not blocked
        result = None
        for i in range(30):
            m.on_base_pose([i * 0.01, 0.0, 0.0], i * self.STEP_NS)
            out = m.on_tick()
            if out and out[0][0] == "nav_result":
                result = out[0][1]
                break
        # 0.4 sim s elapsed at stamp 20 (regardless of how many wall ticks)
        assert result == {"status": "fail", "failure": "timeout", "t_end": 0.4}

    def test_blocked_when_no_progress(self):
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        result = None
        for i in range(10):
            m.on_base_pose([1.0, 0.0, 0.0], i * self.STEP_NS)  # stuck: sim
            out = m.on_tick()  # advances, the pose never does
            if out and out[0][0] == "nav_result":
                result = out[0][1]
                break
        assert result is not None and result["failure"] == "blocked"

    def test_stall_needs_sim_evidence_not_wall_ticks(self):
        """CON-5: wall ticks WITHOUT fresh sim stamps must never fail a
        leg — a slow host that ticks many times between base_pose updates
        would otherwise fake a stall."""
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([1.0, 0.0, 0.0], 0)
        for _ in range(50):  # many wall ticks, sim frozen at stamp 0
            out = m.on_tick()
            assert not (out and out[0][0] == "nav_result"), out

    def test_unstamped_pose_holds_budgets_instead_of_jumping(self):
        """Issue #160 item 1 (PR #159 review; MOB-2 + the BG-3 trust
        boundary extended to nav): a pose with NO usable sim stamp (None
        from parse_sim_stamp — absent, zero, or malformed on the wire)
        updates geometry but HOLDS the machine's clock: stall/timeout
        budgets freeze, they neither anchor at garbage nor accrue."""
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([1.0, 0.0, 0.0], self.STEP_NS)  # stamped, stuck
        m.on_tick()
        held = m._sim_ns
        for _ in range(50):  # a long unstamped stretch, still stuck
            m.on_base_pose([1.0, 0.0, 0.0], None)
            out = m.on_tick()
            assert not (out and out[0][0] == "nav_result"), out
            # HELD, not re-anchored: an `int(stamp or 0)` implementation
            # would drop the clock to 0 here and still pass the assertions
            # above (PR #177 review)
            assert m._sim_ns == held
        # stamps return: the stall budget resumes from the HELD clock and
        # fails blocked once the stuck pose has stall_s of sim evidence
        m.on_base_pose([1.0, 0.0, 0.0], self.STEP_NS + int(0.2e9))
        out = m.on_tick()
        assert out and out[0][0] == "nav_result" and out[0][1]["failure"] == "blocked"

    def test_never_stamped_goal_still_controls_and_arrives(self):
        """Issue #160 item 1 (MOB-2): a fully-unstamped source never starts
        the sim budgets (no sim clock exists to measure them), but control
        and arrival are pure geometry — the goal still completes. The #182
        blind budget does not change this: it is checked AFTER arrival, so
        it bounds blind STEERING, never a goal that got there
        (test_arrival_wins_over_the_blind_budget)."""
        m = self._machine()
        m.on_goal([1.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([0.0, 0.0, 0.0], None)
        out = m.on_tick()
        assert out and out[0][0] == "nav_feedback"  # feedback flows unstamped
        m.on_base_pose([0.95, 0.0, 0.0], None)
        out = m.on_tick()
        assert out and out[0][0] == "nav_result"
        # no clock ever existed: t_end honestly reports 0.0, not a guess
        assert out[0][1] == {"status": "success", "failure": None, "t_end": 0.0}

    def test_new_goal_does_not_inherit_prior_goal_clock(self):
        """MOB-2/CON-5 (PR #177 review): an unstamped first pose on a
        sequential goal must not anchor its budgets to the previous goal's
        last sim stamp. When valid stamps resume, the new goal starts its own
        clock instead of immediately failing from cross-goal elapsed time."""
        m = self._machine()
        m.on_goal([1.0, 0.0, 0.0], "nav-1")
        m.on_base_pose([0.95, 0.0, 0.0], 1_000_000_000)
        assert m.on_tick()[0][1]["status"] == "success"

        m.on_goal([5.0, 0.0, 0.0], "nav-2")
        m.on_base_pose([1.0, 0.0, 0.0], None)
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"
        assert m._t0_ns is None

        m.on_base_pose([1.0, 0.0, 0.0], 2_000_000_000)
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"
        assert m._t0_ns == 2_000_000_000

    def test_blind_nav_fails_closed_instead_of_steering_forever(self):
        """MOB-2, CON-5 (issue #182): a nav goal whose poses never carry a
        usable stamp cannot enforce its own stall or timeout budget, so it
        must not keep steering. After BLIND_POSE_BUDGET consecutive
        unstamped poses the goal ends with `no_sim_clock` and the node
        zeroes the base."""
        from aisle.mobility.nav import BLIND_POSE_BUDGET

        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        for _ in range(BLIND_POSE_BUDGET - 1):
            m.on_base_pose([1.0, 0.0, 0.0], None)
            out = m.on_tick()
            assert not (out and out[0][0] == "nav_result"), "failed before its budget"

        m.on_base_pose([1.0, 0.0, 0.0], None)
        out = m.on_tick()
        assert out and out[0][0] == "nav_result"
        assert out[0][1]["failure"] == "no_sim_clock"
        assert m.target is None  # the goal is released, not left latched

    def test_a_returning_stamp_clears_the_blind_streak(self):
        """MOB-2, CON-5: the budget counts CONSECUTIVE blind poses — a
        transient gap must not accumulate across healthy stretches into a
        spurious failure. Three near-budget gaps, each ended by one good
        stamp, total 3x the budget, so an implementation that never resets
        the streak fails somewhere in here."""
        from aisle.mobility.nav import BLIND_POSE_BUDGET

        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        emitted = []
        for cycle in range(3):
            for _ in range(BLIND_POSE_BUDGET - 1):
                m.on_base_pose([1.0, 0.0, 0.0], None)
                emitted += m.on_tick()  # collect INSIDE the gap, not just after
            m.on_base_pose([1.0, 0.0, 0.0], (cycle + 1) * self.STEP_NS)
            emitted += m.on_tick()
            assert m.target is not None, f"goal released during gap {cycle}"

        blind_failures = [
            e for e in emitted if e[0] == "nav_result" and e[1].get("failure") == "no_sim_clock"
        ]
        assert not blind_failures, blind_failures

    def test_arrival_wins_over_the_blind_budget(self):
        """MOB-2 (issue #182 review): arrival is pure geometry and needs no
        clock, so a goal that actually REACHED its target has succeeded even
        if it did so on the very pose that exhausts the blind budget.
        Failing a completed goal would contradict
        test_never_stamped_goal_still_controls_and_arrives; the budget
        bounds blind STEERING, not blind arriving."""
        from aisle.mobility.nav import BLIND_POSE_BUDGET

        m = self._machine()
        m.on_goal([1.0, 0.0, 0.0], "nav-1")
        # burn the budget down to its last pose, parked away from the target
        for _ in range(BLIND_POSE_BUDGET - 1):
            m.on_base_pose([0.0, 0.0, 0.0], None)
            m.on_tick()
        # the pose that exhausts the budget is ALSO the one that arrives
        m.on_base_pose([1.0, 0.0, 0.0], None)
        out = m.on_tick()
        assert out and out[0][0] == "nav_result"
        assert out[0][1]["status"] == "success", out[0][1]
        assert out[0][1]["failure"] is None

    def test_blind_budget_is_generous_against_the_guard_backstop(self):
        """MOB-2/MOB-3, CON-5 (issue #182 review). Pins the MAGNITUDE, which
        the two tests above cannot: they import BLIND_POSE_BUDGET and loop
        against it, so they follow the constant anywhere — 100000 kept them
        green while removing the net entirely.

        The two bounds have different jobs and different clocks. The guard's
        blind-drive stop is WALL (base_wall_backstop_s) and is the safety
        bound; this budget is SIM (poses / MOB-1 50 Hz) and only ends the
        goal with a diagnosis. So the requirement is NOT "nav fires first" —
        at the store profile's rtf~0.07 it emphatically does not — but that
        the budget is comfortably longer than any transient gap and still
        far short of the BG-2 episode budget."""
        from aisle.mobility.guard import load_base_limits
        from aisle.mobility.nav import BLIND_POSE_BUDGET

        pose_hz = 50.0  # MOB-1 base_pose cadence, in SIM
        sim_s = BLIND_POSE_BUDGET / pose_hz
        assert sim_s == pytest.approx(5.0), "budget moved; re-derive the rtf note on the constant"
        # far above a transient: at least an order of magnitude past the
        # ~0.1 sim-s scale of a dropped frame or two
        assert sim_s >= 1.0
        # and still far inside the episode budget it must never reach --
        # even on the SLOWEST profile on record (store, rtf~0.07)
        assert sim_s / 0.07 < 300.0
        # the guard's own net is wall-clocked and independent of this
        assert load_base_limits("mobile").base_wall_backstop_s == 10.0

    def test_yaw_must_converge_before_success(self):
        """MOB-2 (PR #14 re-review): a pose goal is NOT complete on x/y alone
        — orientation must converge too. At the target position with the
        wrong yaw the action keeps running until the yaw is within tolerance."""
        from aisle.mobility.nav import NavStateMachine

        m = NavStateMachine(arrival_tol_m=0.1, timeout_s=1.0, stall_s=1.0, arrival_yaw_rad=0.1)
        m.on_goal([0.0, 0.0, 1.5708], "y1")
        m.on_base_pose([0.0, 0.0, 0.0], 0)  # in position, wrong orientation
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"  # NOT success — yaw not converged
        assert set(out[0][1]) == {"t", "dist_remaining"}  # MOB-2 contract shape
        m.on_base_pose([0.0, 0.0, 1.55], self.STEP_NS)  # rotated near target
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

        return NavStateMachine(arrival_tol_m=0.05, timeout_s=3.0, stall_s=1.0, arrival_yaw_rad=0.05)

    def test_latch_engages_inside_and_holds_at_boundary(self):
        m = self._machine()
        m.on_goal([1.0, 0.0, 1.5708], "g")
        m.on_base_pose([0.96, 0.0, 0.0], 0)  # inside the radius
        m.on_tick()
        assert m.rotating
        m.on_base_pose([0.93, 0.0, 0.5], 20_000_000)  # just past tol (< 2x)
        m.on_tick()
        assert m.rotating  # hysteresis holds
        m.on_base_pose([0.80, 0.0, 0.5], 40_000_000)  # well outside (> 2x)
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
        for i in range(100):
            m.on_base_pose(pose, i * 20_000_000)
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

    m = NavStateMachine(arrival_tol_m=0.05, timeout_s=20.0, stall_s=1.0, arrival_yaw_rad=0.05)
    m.on_goal([1.0, 0.0, 0.0], "g")
    yaw = 3.0  # facing away; distance will not change while turning
    result = None
    for i in range(300):
        m.on_base_pose([0.0, 0.0, yaw], i * 20_000_000)
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

    STEP_NS = 20_000_000

    def _machine(self):
        from aisle.mobility.nav import NavStateMachine

        return NavStateMachine(
            arrival_tol_m=0.05,
            timeout_s=40.0,
            stall_s=0.1,
            arrival_yaw_rad=0.05,
            capture_tol_m=0.075,
        )

    def test_drive_stall_inside_capture_hands_off_to_rotate_then_succeeds(self):
        # the S1 gate failure verbatim: parked 0.0505 m out, yaw ~pi off
        m = self._machine()
        m.on_goal([-0.5, 0.0, 3.14], "nav-1")
        for i in range(7):  # exhaust the drive-phase stall window (0.1 sim s)
            m.on_base_pose([-0.4995, -0.0505, -0.02], i * self.STEP_NS)
            out = m.on_tick()
            assert not (out and out[0][0] == "nav_result"), out
        assert m.rotating  # captured: final-rotate, not blocked
        m.on_base_pose([-0.4995, -0.0505, 3.13], 8 * self.STEP_NS)  # rotated
        out = m.on_tick()
        assert out[0][0] == "nav_result" and out[0][1]["status"] == "success"

    def test_drive_stall_outside_capture_still_fails_blocked(self):
        m = self._machine()
        m.on_goal([5.0, 0.0, 0.0], "nav-1")
        result = None
        for i in range(10):
            m.on_base_pose([1.0, 0.0, 0.0], i * self.STEP_NS)  # 4 m out, stuck
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
        m.on_base_pose([0.04, 0.0, 0.0], 0)  # dist 0.06: in capture, driving
        out = m.on_tick()
        assert out[0][0] == "nav_feedback"
        assert not m.rotating

    def test_capture_tol_defaults_to_1p5x_arrival(self):
        from aisle.mobility.nav import NavStateMachine

        m = NavStateMachine(arrival_tol_m=0.1, timeout_s=1.0, stall_s=0.1, arrival_yaw_rad=0.1)
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
