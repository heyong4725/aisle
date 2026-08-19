"""Unit tests for the T4 human-sim script and node core (ADR-32
increment one; design doc §8.4.2) — no dora, no sim (CON-12)."""

import pytest

from aisle.nodes.human_sim import (
    HumanSim,
    corrected_med,
    final_target,
    is_corrected,
    requested_med,
)
from aisle.scenes.pharmacy import MED_NAMES

pytestmark = pytest.mark.unit


class TestScript:
    def test_requested_med_is_seed_derived(self):
        """ADR-32 §2: A's identity per seed is seed-derived, so the
        human-sim needs no goal edge (CON-5)."""
        for seed in range(12):
            assert requested_med(seed) == MED_NAMES[seed % len(MED_NAMES)]

    def test_correction_predicate_is_seed_mod_4(self):
        """ADR-32 §2: corrected seeds are exactly seed % 4 == 0."""
        assert [s for s in range(9) if is_corrected(s)] == [0, 4, 8]

    def test_corrected_med_is_next_in_scn2_list_cyclic(self):
        """ADR-32 §2: B is the med after A in SCN-2's fixed name list,
        cyclic, so B != A always."""
        for seed in range(2 * len(MED_NAMES)):
            med_a, med_b = requested_med(seed), corrected_med(seed)
            assert med_b == MED_NAMES[(MED_NAMES.index(med_a) + 1) % len(MED_NAMES)]
            assert med_b != med_a

    def test_final_target_is_b_on_corrected_seeds_else_a(self):
        """ADR-32 §1: the TC-7 goal's target_med is the FINAL corrected
        target — B on corrected seeds, else A."""
        for seed in range(12):
            expected = corrected_med(seed) if seed % 4 == 0 else requested_med(seed)
            assert final_target(seed) == expected


class TestHumanSim:
    def request(self, human, seed=1, goal_id="ep-0000"):
        return human.on_episode_meta({"goal_id": goal_id, "seed": seed}, goal_id)

    def test_episode_meta_emits_request_naming_a(self):
        """ADR-32 §2 step 1: a new episode speaks `request` naming A,
        goal_id-correlated (TC-7)."""
        human = HumanSim()
        emissions = self.request(human, seed=1)
        assert len(emissions) == 1
        topic, payload, metadata = emissions[0]
        assert topic == "human_msg"
        assert payload["kind"] == "request"
        assert payload["med"] == requested_med(1)
        assert metadata == {"goal_id": "ep-0000"}

    def test_confirm_on_uncorrected_seed_gets_yes(self):
        """ADR-32 §2 step 2: on the robot's confirm for A the reply is
        confirm_reply — seed 1 is not corrected."""
        human = HumanSim()
        self.request(human, seed=1)
        emissions = human.on_robot_msg({"kind": "confirm", "med": requested_med(1)}, "ep-0000")
        assert [(e[0], e[1]["kind"]) for e in emissions] == [("human_msg", "confirm_reply")]

    def test_confirm_on_corrected_seed_gets_correction_naming_b(self):
        """ADR-32 §2 step 2, corrected branch: seed % 4 == 0 answers the
        confirm with a correction naming B."""
        human = HumanSim()
        self.request(human, seed=4)
        emissions = human.on_robot_msg({"kind": "confirm", "med": requested_med(4)}, "ep-0000")
        ((topic, payload, _),) = emissions
        assert topic == "human_msg"
        assert payload["kind"] == "correction"
        assert payload["med"] == corrected_med(4)

    def test_reconfirm_of_b_gets_yes(self):
        """ADR-32 §2: the robot re-confirms B once; the human answers yes."""
        human = HumanSim()
        self.request(human, seed=4)
        human.on_robot_msg({"kind": "confirm", "med": requested_med(4)}, "ep-0000")
        emissions = human.on_robot_msg({"kind": "confirm", "med": corrected_med(4)}, "ep-0000")
        ((_, payload, _),) = emissions
        assert payload["kind"] == "confirm_reply"
        assert payload["med"] == corrected_med(4)

    def test_unexpected_confirm_gets_no_reply(self):
        """ADR-32 §2: the script keys on the EXPECTED confirm — a confirm
        naming the wrong med, the wrong goal, or arriving before any
        request gets silence, and the episode times out honestly."""
        human = HumanSim()
        assert human.on_robot_msg({"kind": "confirm", "med": MED_NAMES[0]}, "ep-0000") == []
        self.request(human, seed=1)
        wrong_med = corrected_med(1)
        assert human.on_robot_msg({"kind": "confirm", "med": wrong_med}, "ep-0000") == []
        wrong_goal = {"kind": "confirm", "med": requested_med(1)}
        assert human.on_robot_msg(wrong_goal, "ep-9999") == []

    def test_done_dialogue_ignores_further_confirms(self):
        """A completed script replies to nothing more this episode."""
        human = HumanSim()
        self.request(human, seed=1)
        human.on_robot_msg({"kind": "confirm", "med": requested_med(1)}, "ep-0000")
        assert human.on_robot_msg({"kind": "confirm", "med": requested_med(1)}, "ep-0000") == []

    def test_new_episode_meta_resets_the_script(self):
        """The next episode's meta restarts the script cleanly."""
        human = HumanSim()
        self.request(human, seed=1)
        human.on_robot_msg({"kind": "confirm", "med": requested_med(1)}, "ep-0000")
        emissions = human.on_episode_meta({"goal_id": "ep-0001", "seed": 2}, "ep-0001")
        ((_, payload, metadata),) = emissions
        assert payload["kind"] == "request"
        assert payload["med"] == requested_med(2)
        assert metadata == {"goal_id": "ep-0001"}

    def test_script_is_deterministic(self):
        """CON-5: same seed and same robot_msg sequence produce the same
        message trace."""

        def trace(seed):
            human = HumanSim()
            out = list(human.on_episode_meta({"goal_id": "ep-0000", "seed": seed}, "ep-0000"))
            out += human.on_robot_msg({"kind": "confirm", "med": requested_med(seed)}, "ep-0000")
            if is_corrected(seed):
                out += human.on_robot_msg(
                    {"kind": "confirm", "med": corrected_med(seed)}, "ep-0000"
                )
            return out

        for seed in (0, 1, 4, 7):
            assert trace(seed) == trace(seed)


class TestRecoveryScript:
    """T4 inc-2 (VER-3 amendment, ADR-32 §3 epoch): the recovery meta
    scripts step 3 — the human names the wrong med for RETURN and
    requests the correction, then the normal confirm flow proceeds."""

    def test_recovery_meta_requests_correction_and_return(self):
        human = HumanSim()
        out = human.on_episode_meta(
            {"goal_id": "ep-0000r", "seed": 4, "recovery": True}, "ep-0000r"
        )
        ((topic, payload, metadata),) = out
        assert topic == "human_msg" and payload["kind"] == "request"
        assert payload["med"] == corrected_med(4)
        assert payload["return_med"] == requested_med(4)
        assert "wrong one" in payload["text"]
        assert metadata == {"goal_id": "ep-0000r"}

    def test_recovery_confirm_flow_is_the_normal_exchange(self):
        human = HumanSim()
        human.on_episode_meta({"goal_id": "g", "seed": 4, "recovery": True}, "g")
        out = human.on_robot_msg({"kind": "confirm", "med": corrected_med(4)}, "g")
        assert out[0][1]["kind"] == "confirm_reply"
