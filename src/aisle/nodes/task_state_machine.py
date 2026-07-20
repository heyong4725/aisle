"""task-state-machine node (CAP-5): episode sequencing per TC-7.

A goal opens an episode: emit one target_request naming the med, then
>=1 Hz episode_feedback until the verifier's episode_result closes it.
Violations from the guard are counted into the feedback. In-context
retries (HAR-3, max_retries) are Phase 2 — one attempt per goal at M0
(ADR-10).
"""

from __future__ import annotations


class TaskStateMachine:
    """Pure core: each handler returns [(topic, payload, goal_id), ...]."""

    def __init__(self) -> None:
        self.goal: dict | None = None
        self.goal_id: str | None = None
        self.violations: dict[str, int] = {}
        self.ticks = 0

    def on_goal(self, goal: dict, goal_id: str) -> list:
        if self.goal is not None:  # TC-7: actions do not overlap
            return []
        self.goal, self.goal_id, self.violations = goal, goal_id, {}
        self.ticks = 0
        return [("target_request", {"target_med": goal["target_med"]}, goal_id)]

    def on_tick(self) -> list:
        """Feedback t = 1 Hz ticks since the goal (CON-5: deterministic —
        a wall-clock read would make same-seed runs emit different
        payloads, and would span episodes rather than the current one)."""
        if self.goal is None:
            return []
        self.ticks += 1
        feedback: dict = {"t": self.ticks, "phase": "executing"}
        if self.violations:
            feedback["violations"] = dict(self.violations)
        return [("episode_feedback", feedback, self.goal_id)]

    def on_result(self) -> list:
        self.goal = None
        return []

    def on_violation(self, violation: dict) -> None:
        reason = violation.get("reason", "unknown")
        self.violations[reason] = self.violations.get(reason, 0) + 1


def main() -> None:
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.topics import make_sender

    node = Node()
    send = make_sender(node)
    machine = TaskStateMachine()

    def emit(emissions) -> None:
        for topic, payload, goal_id in emissions:
            send(topic, pa.array([json.dumps(payload)]), {"goal_id": goal_id})

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            emissions = machine.on_goal(goal, metadata.get("goal_id", ""))
            if not emissions:
                print(f"goal {metadata.get('goal_id')} refused: episode active", file=sys.stderr)
            emit(emissions)
        elif event["id"] == "episode_result":
            emit(machine.on_result())
        elif event["id"] == "violation":
            machine.on_violation(json.loads(event["value"][0].as_py()))
        elif event["id"] == "tick":
            emit(machine.on_tick())


if __name__ == "__main__":
    main()
