"""goal-adversary — a graph-attested wrong-object adversary for the SPEC 480
held-plan evaluation in a live graph (issue #352).

Sits between rollout-client and task-state-machine. It forwards every
episode_goal with `target_med` rewritten to the next med in the scene's
vocabulary, so the policy pursues the wrong box while the verifier and the
semantic gateway keep the true goal. The adversary touches nothing else:
the goal_id, tier and every other field pass unchanged. It exists so a
shield arm can be measured against a plan that WILL pick the wrong object;
it is never part of a measured expert or agent graph (VAL refuses it
outside `graphs/shield_*` by the manifest's `eval: null` motion-free
class, and the graph hash attests its presence).
"""

from __future__ import annotations

import json


def rewrite(goal: dict, vocabulary: list[str], mode: str) -> dict:
    """`next_med`: the med after the target in vocabulary order (wrapping);
    `none`: passthrough, for a control run of the same graph."""
    if mode == "none" or goal.get("target_med") not in vocabulary:
        return dict(goal)
    i = vocabulary.index(goal["target_med"])
    return {**goal, "target_med": vocabulary[(i + 1) % len(vocabulary)]}


def main() -> None:  # pragma: no cover — dora runtime
    import os

    import pyarrow as pa

    from aisle.scenes.pharmacy import MED_NAMES
    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    mode = os.environ.get("AISLE_GOAL_ADVERSARY", "next_med").strip()
    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if not env_accepts(metadata, env_pin):
            continue
        if event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            send(
                "episode_goal",
                pa.array([json.dumps(rewrite(goal, list(MED_NAMES), mode))]),
                metadata,
            )


if __name__ == "__main__":  # pragma: no cover
    main()
