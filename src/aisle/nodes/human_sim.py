"""human-sim node (ADR-32): the T4 scripted human-request generator.

Design doc §8.4.2: T4 runs are reproducible because the human is a
SCRIPT — a pure function of the per-goal seed and the observed
`robot_msg` sequence (CON-5). Per goal: (1) emit `request` naming med A;
(2) on the robot's `confirm` for A, reply `confirm_reply` yes — except
on corrected seeds (seed % 4 == 0), where the reply is a `correction`
naming med B, the med after A in SCN-2's fixed name list (cyclic, so
B != A always); (3) on the robot's re-confirm for B, reply yes.

The node receives `{goal_id, seed}` at episode start via `episode_meta`
from the rollout client — a FORWARD edge carrying no target information
beyond what the script derives (ADR-32 §2). It never sees the TC-7
goal: the goal's `target_med` is the FINAL corrected target, consumed
only by verifier-* nodes (validator rule DIALOGUE_GOAL_LEAK).

The script functions below are the single source of the seed-derived
artifacts (A, the corrected set, B); the rollout client imports them to
set the goal's `target_med`, so human and verifier can never disagree
about what a seed asks for.
"""

from __future__ import annotations

from aisle.scenes.pharmacy import MED_NAMES

# ADR-32 §2: the correction predicate on TC-7's per-goal seed
CORRECTION_MODULUS = 4


def requested_med(seed: int) -> str:
    """Med A: what the human asks for first (seed-derived, ADR-32 §2)."""
    return MED_NAMES[seed % len(MED_NAMES)]


def is_corrected(seed: int) -> bool:
    return seed % CORRECTION_MODULUS == 0


def corrected_med(seed: int) -> str:
    """Med B: the med after A in SCN-2's fixed list, cyclic (B != A)."""
    return MED_NAMES[(seed + 1) % len(MED_NAMES)]


def final_target(seed: int) -> str:
    """What the episode must deliver — the TC-7 goal's target_med."""
    return corrected_med(seed) if is_corrected(seed) else requested_med(seed)


class HumanSim:
    """Pure core: each handler returns [(topic, payload, metadata), ...]."""

    def __init__(self) -> None:
        self.goal_id: str | None = None
        self.seed: int | None = None
        self.phase = "idle"  # -> awaiting_confirm -> (awaiting_reconfirm) -> done
        self.expected: str | None = None  # the med the next confirm must name

    def on_episode_meta(self, payload: dict, goal_id: str) -> list:
        """A new episode: reset the script and speak the request."""
        seed = int(payload["seed"])
        self.goal_id, self.seed = goal_id, seed
        med = requested_med(seed)
        self.phase, self.expected = "awaiting_confirm", med
        return [
            (
                "human_msg",
                {"kind": "request", "med": med, "text": f"please bring me the {med}"},
                {"goal_id": goal_id},
            )
        ]

    def on_robot_msg(self, payload: dict, goal_id: str) -> list:
        """The script keys on the EXPECTED confirm (ADR-32 §2); anything
        else — wrong med, wrong goal, wrong phase — gets no reply, and the
        episode closes honestly on the verifier's timeout."""
        if goal_id != self.goal_id or payload.get("kind") != "confirm":
            return []
        if payload.get("med") != self.expected:
            return []
        if self.phase == "awaiting_confirm":
            assert self.seed is not None
            if is_corrected(self.seed):
                med_b = corrected_med(self.seed)
                self.phase, self.expected = "awaiting_reconfirm", med_b
                return [
                    (
                        "human_msg",
                        {"kind": "correction", "med": med_b, "text": f"no, the {med_b} please"},
                        {"goal_id": goal_id},
                    )
                ]
            self.phase = "done"
            return [
                (
                    "human_msg",
                    {"kind": "confirm_reply", "med": self.expected, "text": "yes"},
                    {"goal_id": goal_id},
                )
            ]
        if self.phase == "awaiting_reconfirm":
            self.phase = "done"
            return [
                (
                    "human_msg",
                    {"kind": "confirm_reply", "med": self.expected, "text": "yes"},
                    {"goal_id": goal_id},
                )
            ]
        return []


def main() -> None:
    import json
    import os

    import pyarrow as pa

    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)
    human = HumanSim()

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if not env_accepts(metadata, env_pin):
            continue  # fleet mode (BRG-5): another env's stream
        if event["id"] not in {"episode_meta", "robot_msg"}:
            continue  # ADR-30 scheduler/wall inputs carry no dialogue payload
        payload = json.loads(event["value"][0].as_py())
        goal_id = metadata.get("goal_id", "")
        if event["id"] == "episode_meta":
            emissions = human.on_episode_meta(payload, goal_id)
        else:
            emissions = human.on_robot_msg(payload, goal_id)
        for topic, out_payload, out_metadata in emissions:
            send(topic, pa.array([json.dumps(out_payload)]), out_metadata)


if __name__ == "__main__":
    main()
