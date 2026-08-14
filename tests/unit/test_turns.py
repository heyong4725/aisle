"""ADR-30 lockstep protocol unit acceptance (issue #175)."""

from __future__ import annotations

import pytest

from aisle.turns import (
    BridgeTurn,
    ParticipantTurn,
    ProtocolError,
    TurnBarrier,
    TurnStamp,
    TurnWatchdog,
    parse_turn_stamp,
    watermark_metadata,
)


def _stamp(turn_id: int, *, epoch: int = 7, sim_time_ns: int = 0) -> dict:
    return {"turn_epoch": epoch, "turn_id": turn_id, "sim_time_ns": sim_time_ns, "seq": 0}


def _watermark(turn_id: int, outputs: dict[str, int], *, epoch: int = 7) -> dict:
    return watermark_metadata(TurnStamp(epoch, turn_id, turn_id * 10_000_000), outputs)


def _commit(turn_id: int, counts: dict[str, int], *, epoch: int, sim_time_ns: int) -> dict:
    names = sorted(counts)
    return {
        **_stamp(turn_id, epoch=epoch, sim_time_ns=sim_time_ns),
        "expected_inputs": names,
        "expected_counts": [counts[name] for name in names],
    }


class TestTurnStamp:
    @pytest.mark.parametrize(
        "metadata",
        [
            {},
            {"turn_epoch": 1, "turn_id": 0},
            {"turn_epoch": True, "turn_id": 0, "sim_time_ns": 0},
            {"turn_epoch": 1, "turn_id": False, "sim_time_ns": 0},
            {"turn_epoch": 0, "turn_id": 0, "sim_time_ns": 0},
            {"turn_epoch": 1, "turn_id": -1, "sim_time_ns": 0},
            {"turn_epoch": 1, "turn_id": 0, "sim_time_ns": -1},
            {"turn_epoch": "bad", "turn_id": 0, "sim_time_ns": 0},
        ],
    )
    def test_total_parser_refuses_missing_zero_malformed_and_negative_fields(self, metadata):
        """TC-2/BRG-1: the lockstep trust boundary is total and fail closed."""
        assert parse_turn_stamp(metadata) is None

    def test_turn_zero_and_sim_time_zero_are_valid_inside_a_nonzero_epoch(self):
        """TC-2/BRG-1: the bridge opens turn zero before taking a physics step."""
        assert parse_turn_stamp(_stamp(0)) == TurnStamp(7, 0, 0)


class TestWatermark:
    def test_watermark_enumerates_every_output_in_lexical_order_including_zero(self):
        """CAP-1: zero count is ordinary and cannot be confused with omission."""
        metadata = _watermark(3, {"z": 2, "a": 0, "middle": 1})
        assert metadata["closed_outputs"] == ["a", "middle", "z"]
        assert metadata["emitted_counts"] == [0, 1, 2]

    @pytest.mark.parametrize(
        "mutation",
        [
            {"closed_outputs": ["b", "a"], "emitted_counts": [0, 0]},
            {"closed_outputs": ["a"], "emitted_counts": []},
            {"closed_outputs": ["a", "a"], "emitted_counts": [0, 0]},
            {"closed_outputs": ["a"], "emitted_counts": [-1]},
            {"closed_outputs": ["a"], "emitted_counts": [True]},
        ],
    )
    def test_malformed_watermark_is_a_protocol_error(self, mutation):
        """CAP-1/BRG-1: malformed closure is loud, never inferred from arrival order."""
        plan = {
            "bridge": "bridge",
            "participants": {"worker": {"inputs": {}}},
        }
        barrier = TurnBarrier(plan)
        barrier.open_bridge(_watermark(0, {"sim_turn": 1}))
        bad = {**_watermark(0, {"a": 0}), **mutation}
        with pytest.raises(ProtocolError):
            barrier.close("worker", bad)


class TestTurnBarrier:
    @staticmethod
    def _plan() -> dict:
        return {
            "bridge": "bridge",
            "bridge_outputs": ["sim_turn", "state"],
            "bridge_inputs": {
                "joint_cmd": {"source": "driver", "output": "command"},
            },
            "participants": {
                "client": {
                    "outputs": ["goal", "turn_done"],
                    "inputs": {
                        "episode_result": {
                            "source": "verifier",
                            "output": "episode_result",
                            "edge": "episodic",
                        }
                    },
                },
                "planner": {
                    "outputs": ["command", "turn_done"],
                    "inputs": {"goal": {"source": "client", "output": "goal", "edge": "forward"}},
                },
                "verifier": {
                    "outputs": ["episode_result", "turn_done"],
                    "inputs": {
                        "state": {"source": "bridge", "output": "state", "edge": "forward"},
                        "goal": {"source": "client", "output": "goal", "edge": "forward"},
                    },
                },
                "driver": {
                    "outputs": ["command", "turn_done"],
                    "inputs": {
                        "command": {
                            "source": "planner",
                            "output": "command",
                            "edge": "forward",
                        }
                    },
                },
            },
        }

    def test_forward_nodes_open_only_after_upstreams_close_with_exact_counts(self):
        """BRG-1/CAP-1: readiness follows declared counts, not wall arrival order."""
        barrier = TurnBarrier(self._plan())
        assert barrier.open_bridge(_watermark(0, {"sim_turn": 1, "state": 1})) == {
            "client": {"episode_result": 0}
        }
        assert barrier.close("client", _watermark(0, {"goal": 1, "turn_done": 1})) == {
            "planner": {"goal": 1},
            "verifier": {"goal": 1, "state": 1},
        }
        assert barrier.close("planner", _watermark(0, {"command": 1, "turn_done": 1})) == {
            "driver": {"command": 1}
        }
        assert barrier.close("driver", _watermark(0, {"command": 1, "turn_done": 1})) == {}
        assert barrier.close("verifier", _watermark(0, {"episode_result": 1, "turn_done": 1})) == {}
        assert barrier.complete
        assert barrier.bridge_expected_inputs() == {"joint_cmd": 1}

    def test_episodic_result_is_consumed_exactly_one_turn_later(self):
        """BRG-1/VAL-2: reply cycles terminate through a one-turn episodic delay."""
        barrier = TurnBarrier(self._plan())
        barrier.open_bridge(_watermark(0, {"sim_turn": 1, "state": 1}))
        barrier.close("client", _watermark(0, {"goal": 0, "turn_done": 1}))
        barrier.close("planner", _watermark(0, {"command": 0, "turn_done": 1}))
        barrier.close("driver", _watermark(0, {"command": 0, "turn_done": 1}))
        barrier.close("verifier", _watermark(0, {"episode_result": 1, "turn_done": 1}))
        assert barrier.complete

        ready = barrier.open_bridge(_watermark(1, {"sim_turn": 1, "state": 1}))
        assert ready == {"client": {"episode_result": 1}}

    @pytest.mark.parametrize(
        ("node", "metadata"),
        [
            ("client", _watermark(0, {"goal": 0, "turn_done": 1})),  # duplicate
            ("client", _watermark(1, {"goal": 0, "turn_done": 1})),  # future
            ("client", _watermark(0, {"goal": 0, "turn_done": 1}, epoch=8)),
        ],
    )
    def test_duplicate_future_and_cross_epoch_closures_fail_loudly(self, node, metadata):
        """BRG-1: invalid closures never manufacture a commit or advance physics."""
        barrier = TurnBarrier(self._plan())
        barrier.open_bridge(_watermark(0, {"sim_turn": 1, "state": 1}))
        barrier.close("client", _watermark(0, {"goal": 0, "turn_done": 1}))
        with pytest.raises(ProtocolError):
            barrier.close(node, metadata)
        assert not barrier.complete

    def test_omitted_or_invented_watermark_ports_fail_loudly(self):
        """CAP-1: closure must enumerate the graph's complete output set."""
        barrier = TurnBarrier(self._plan())
        with pytest.raises(ProtocolError, match="bridge watermark output set"):
            barrier.open_bridge(_watermark(0, {"sim_turn": 1}))

        barrier.open_bridge(_watermark(0, {"sim_turn": 1, "state": 1}))
        with pytest.raises(ProtocolError, match="client watermark output set"):
            barrier.close(
                "client",
                _watermark(0, {"goal": 1, "invented": 0, "turn_done": 1}),
            )

    def test_shutdown_request_is_recorded_only_on_a_valid_closure(self):
        """BRG-1: finite runs close their last turn before bridge teardown."""
        barrier = TurnBarrier(self._plan())
        barrier.open_bridge(_watermark(0, {"sim_turn": 1, "state": 1}))
        barrier.close(
            "client",
            {**_watermark(0, {"goal": 0, "turn_done": 1}), "shutdown": True},
        )
        assert barrier.shutdown_requested is True


class TestBridgeTurn:
    @staticmethod
    def _commit_metadata(stamp: TurnStamp, **counts: int) -> dict:
        names = sorted(counts)
        return {
            **stamp.metadata(),
            "expected_inputs": names,
            "expected_counts": [counts[name] for name in names],
        }

    def test_commit_declaration_waits_for_cross_port_command_delivery(self):
        """BRG-1: a watermark/commit overtaking data cannot close the turn early."""
        stamp = TurnStamp(3, 9, 90_000_000)
        turn = BridgeTurn(stamp)
        assert turn.commit(self._commit_metadata(stamp, joint_cmd=1)) is None
        assert turn.advances_physics is None

        turn.accept("joint_cmd", "joint", {**stamp.metadata(), "seq": 1})
        assert turn.ready_to_commit
        assert turn.finish() == [("joint_cmd", "joint")]
        assert turn.advances_physics is True

    def test_zero_count_bridge_inputs_close_without_receiving_placeholder_data(self):
        """BRG-1: declared zero means absence; no message is manufactured."""
        stamp = TurnStamp(3, 9, 90_000_000)
        turn = BridgeTurn(stamp)
        assert turn.commit(self._commit_metadata(stamp, joint_cmd=0, reset=0)) == []
        assert turn.advances_physics is True

    def test_commands_coalesce_independently_per_environment(self):
        """BRG-5/BRG-3: one env's command must never replace another env's command."""
        stamp = TurnStamp(3, 9, 90_000_000)
        turn = BridgeTurn(stamp, n_envs=2)
        turn.accept("joint_cmd", "env-0", {**stamp.metadata(), "seq": 1, "env_id": 0})
        turn.accept("joint_cmd", "env-1", {**stamp.metadata(), "seq": 1, "env_id": 1})
        assert turn.commit(self._commit_metadata(stamp, joint_cmd=2)) == [
            ("joint_cmd", "env-0"),
            ("joint_cmd", "env-1"),
        ]

    def test_multi_environment_inputs_require_an_explicit_valid_env_id(self):
        """BRG-5: fleet inputs never fall back silently to environment zero."""
        stamp = TurnStamp(3, 9, 90_000_000)
        turn = BridgeTurn(stamp, n_envs=2)
        with pytest.raises(ProtocolError, match="no env_id"):
            turn.accept("joint_cmd", "missing", {**stamp.metadata(), "seq": 1})
        with pytest.raises(ProtocolError, match="outside"):
            turn.accept("joint_cmd", "out-of-range", {**stamp.metadata(), "seq": 1, "env_id": 2})

    def test_resets_are_unique_per_environment_and_applied_in_env_order(self):
        """BRG-1/BRG-5: each fleet lane may reset once without replacing a neighbour."""
        stamp = TurnStamp(3, 9, 90_000_000)
        turn = BridgeTurn(stamp, n_envs=2)
        turn.accept("reset", "env-1", {**stamp.metadata(), "seq": 1, "env_id": 1})
        turn.accept("reset", "env-0", {**stamp.metadata(), "seq": 1, "env_id": 0})
        assert turn.commit(self._commit_metadata(stamp, reset=2)) == [
            ("reset", "env-0"),
            ("reset", "env-1"),
        ]
        assert turn.advances_physics is False

    def test_commands_are_coalesced_by_seq_and_applied_in_canonical_order(self):
        """BRG-1/BRG-3: arrival order cannot select the simulated trajectory."""
        turn = BridgeTurn(TurnStamp(3, 9, 90_000_000))
        turn.accept(
            "base_cmd", "base-old", {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 1}
        )
        turn.accept("joint_cmd", "joint", {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 4})
        turn.accept(
            "base_cmd", "base-new", {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 2}
        )
        turn.accept("gripper_cmd", "grip", {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 1})

        assert turn.commit(
            _commit(
                9,
                {"base_cmd": 2, "gripper_cmd": 1, "joint_cmd": 1},
                epoch=3,
                sim_time_ns=90_000_000,
            )
        ) == [
            ("joint_cmd", "joint"),
            ("gripper_cmd", "grip"),
            ("base_cmd", "base-new"),
        ]

    def test_coalesced_command_records_dropped_count_on_survivor(self):
        """BRG-3: seq-based coalescing remains visible in command metadata."""
        turn = BridgeTurn(TurnStamp(3, 9, 90_000_000))
        old = {"metadata": {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 1}}
        new = {"metadata": {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 3}}
        stale = {"metadata": {**_stamp(9, epoch=3, sim_time_ns=90_000_000), "seq": 2}}
        turn.accept("joint_cmd", old, old["metadata"])
        turn.accept("joint_cmd", new, new["metadata"])
        turn.accept("joint_cmd", stale, stale["metadata"])
        assert (
            turn.commit(_commit(9, {"joint_cmd": 3}, epoch=3, sim_time_ns=90_000_000))[0][1][
                "metadata"
            ]["dropped"]
            == 2
        )

    def test_reset_discards_same_turn_motion_and_advances_no_physics(self):
        """BRG-1: reset has priority and closes a zero-step turn."""
        turn = BridgeTurn(TurnStamp(2, 4, 40))
        turn.accept("joint_cmd", "motion", {**_stamp(4, epoch=2, sim_time_ns=40), "seq": 1})
        turn.accept("reset", "seed", {**_stamp(4, epoch=2, sim_time_ns=40), "seq": 1})
        assert turn.commit(_commit(4, {"joint_cmd": 1, "reset": 1}, epoch=2, sim_time_ns=40)) == [
            ("reset", "seed")
        ]
        assert turn.advances_physics is False

    @pytest.mark.parametrize(
        "metadata",
        [
            {},
            _stamp(8, epoch=3, sim_time_ns=90_000_000),
            _stamp(10, epoch=3, sim_time_ns=90_000_000),
            _stamp(9, epoch=4, sim_time_ns=90_000_000),
        ],
    )
    def test_unstamped_stale_future_and_cross_epoch_commands_fail_closed(self, metadata):
        """TC-2/BRG-1: an invalid command cannot enter the open turn."""
        turn = BridgeTurn(TurnStamp(3, 9, 90_000_000))
        with pytest.raises(ProtocolError):
            turn.accept("joint_cmd", "motion", {**metadata, "seq": 1})
        assert turn.commands == []

    def test_duplicate_commit_fails(self):
        """BRG-1: exactly one terminal commit closes a turn."""
        turn = BridgeTurn(TurnStamp(3, 9, 90_000_000))
        turn.commit(_commit(9, {}, epoch=3, sim_time_ns=90_000_000))
        with pytest.raises(ProtocolError):
            turn.commit(_commit(9, {}, epoch=3, sim_time_ns=90_000_000))


class TestParticipantTurn:
    def test_data_may_arrive_before_ready_but_is_released_in_canonical_order(self):
        """BRG-1/CAP-1: transport arrival order never becomes handler order."""
        participant = ParticipantTurn("worker", ["z_out", "a_out", "turn_done"])
        participant.buffer("z_input", "z1", _stamp(4, epoch=2, sim_time_ns=40))
        participant.buffer("a_input", "a2", {**_stamp(4, epoch=2, sim_time_ns=40), "seq": 2})
        participant.buffer("a_input", "a1", {**_stamp(4, epoch=2, sim_time_ns=40), "seq": 1})
        assert not participant.ready

        participant.open(
            {
                **_stamp(4, epoch=2, sim_time_ns=40),
                "expected_inputs": ["a_input", "z_input"],
                "expected_counts": [2, 1],
            }
        )
        assert participant.take() == ["a1", "a2", "z1"]

    def test_zero_count_input_closes_without_a_timeout_guess(self):
        """CAP-1: declared zero is sufficient evidence of absence."""
        participant = ParticipantTurn("worker", ["out", "turn_done"])
        participant.open(
            {
                **_stamp(0, epoch=1),
                "expected_inputs": ["optional"],
                "expected_counts": [0],
            }
        )
        assert participant.ready
        assert participant.take() == []

    def test_episodic_input_keeps_prior_producer_stamp_in_next_consumer_turn(self):
        """ADR-30 §1.3/TC-2: k-1 payload is consumed exactly in turn k."""
        participant = ParticipantTurn("client", ["reset", "turn_done"])
        prior = _stamp(4, epoch=2, sim_time_ns=40)
        participant.buffer("episode_result", "verdict", prior)
        participant.open(
            {
                **_stamp(5, epoch=2, sim_time_ns=50),
                "expected_inputs": ["episode_result"],
                "expected_counts": [1],
                "expected_turn_epochs": [2],
                "expected_turn_ids": [4],
                "expected_sim_time_ns": [40],
            }
        )
        assert participant.take() == ["verdict"]

    def test_missing_data_never_becomes_ready_and_extra_data_fails_loudly(self):
        """BRG-1: a dropped edge hangs for the watchdog; duplicates abort."""
        participant = ParticipantTurn("worker", ["out", "turn_done"])
        participant.open(
            {
                **_stamp(1, epoch=1, sim_time_ns=10),
                "expected_inputs": ["input"],
                "expected_counts": [1],
            }
        )
        assert not participant.ready
        participant.buffer("input", "one", _stamp(1, epoch=1, sim_time_ns=10))
        assert participant.ready
        with pytest.raises(ProtocolError):
            participant.buffer("input", "duplicate", _stamp(1, epoch=1, sim_time_ns=10))

    def test_missing_sequence_is_malformed_not_an_implicit_zero(self):
        """TC-2: absent seq cannot alias a producer's legitimate sequence zero."""
        participant = ParticipantTurn("worker", ["out", "turn_done"])
        metadata = _stamp(1, epoch=1, sim_time_ns=10)
        metadata.pop("seq")
        with pytest.raises(ProtocolError, match="malformed seq"):
            participant.buffer("input", "one", metadata)

    def test_close_declares_every_output_and_preserves_turn_stamp(self):
        """TC-2/CAP-1: derived closure preserves epoch, turn, sim time, and zeros."""
        participant = ParticipantTurn("worker", ["z_out", "a_out", "turn_done"])
        participant.open(
            {
                **_stamp(6, epoch=3, sim_time_ns=60),
                "expected_inputs": [],
                "expected_counts": [],
            }
        )
        participant.take()
        participant.record_output("z_out")
        metadata = participant.close()
        assert parse_turn_stamp(metadata) == TurnStamp(3, 6, 60)
        assert metadata["closed_outputs"] == ["a_out", "turn_done", "z_out"]
        assert metadata["emitted_counts"] == [0, 1, 1]

    @pytest.mark.parametrize(
        "metadata",
        [
            {**_stamp(0, epoch=1), "expected_inputs": ["a"], "expected_counts": []},
            {**_stamp(0, epoch=1), "expected_inputs": ["b", "a"], "expected_counts": [0, 0]},
            {**_stamp(0, epoch=1), "expected_inputs": ["a", "a"], "expected_counts": [0, 0]},
            {**_stamp(0, epoch=1), "expected_inputs": ["a"], "expected_counts": [-1]},
        ],
    )
    def test_malformed_ready_declaration_is_refused(self, metadata):
        """BRG-1: readiness metadata is a total trust boundary too."""
        participant = ParticipantTurn("worker", ["out", "turn_done"])
        with pytest.raises(ProtocolError):
            participant.open(metadata)


def test_watchdog_uses_distinct_ordinary_and_verdict_turn_budgets():
    """BRG-1: heavy causal work gets its declared budget; neither timeout advances physics."""
    now = [10.0]
    watchdog = TurnWatchdog(ordinary_s=0.5, verdict_s=5.0, clock=lambda: now[0])
    watchdog.open()
    now[0] = 10.6
    assert watchdog.expired
    assert watchdog.turn_type == "ordinary"

    now[0] = 20.0
    watchdog.open()
    watchdog.mark_verdict_bearing()
    now[0] = 24.9
    assert not watchdog.expired
    now[0] = 25.1
    assert watchdog.expired
    assert watchdog.turn_type == "verdict"
