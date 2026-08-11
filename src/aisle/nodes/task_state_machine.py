"""task-state-machine node (CAP-5): episode sequencing per TC-7.

A goal opens an episode: emit one target_request naming the med, then
>=1 Hz episode_feedback until the verifier's episode_result closes it.
Violations from the guard are counted into the feedback. In-context
retries (HAR-3, max_retries) are Phase 2 — one attempt per goal at M0
(ADR-10).

T2 tier (design doc §3, idea I13): the medicine is identified by its
printed label, so the machine runs a SCAN TOUR before the grasp.
Candidates are the detected target pose plus its neighbour rows (all
box positions — at T2 the detector's identity claims are color-derived
noise; only the positions are trusted). Per candidate: `read_move`
parks the wrist camera at the face (ik-trajectory answers `move_done`
with the ladder range actually used), `read_request` asks the label
reader, and a matching `read_result` promotes the candidate to
`grasp_target` — the same schema the grasp planner consumes as
target_pose, so the grasp pipeline is untouched. A refused read or a
non-matching label advances the tour; an exhausted tour idles and the
episode closes honestly on the verifier's timeout.
"""

from __future__ import annotations

READ_TIER = "T2"
# per candidate: the first read plus this-many-minus-one refusal retries
# from later ladder entries. ONE by measurement: every correct read in
# the offline tour landed on the candidate's FIRST tracked entry, while
# each extra park only added a home->shelf transit — and one such retry
# transit knocked a box 3 cm and closed the first clean live T2 episode
# `collision`. The retry mechanism stays for loop agents to tune.
MAX_READS_PER_CANDIDATE = 1


class TaskStateMachine:
    """Pure core: each handler returns [(topic, payload, metadata), ...].
    Metadata carries goal_id (TC-7) and, for tour topics, request_id
    (TC-6 service pattern)."""

    def __init__(self, tier: str = "T1") -> None:
        self.tier = tier
        self.goal: dict | None = None
        self.goal_id: str | None = None
        self.violations: dict[str, int] = {}
        self.ticks = 0
        self.candidates: list | None = None  # [(pos, face)] tour order
        self.tour_idx = 0
        self.tour_meta: dict | None = None  # target_pose metadata to relay
        self.awaiting: str | None = None  # request_id in flight
        # perception republishes target_pose every frame pair; once a
        # tour has run for this goal it must NOT restart — a fresh
        # read_move racing the promoted grasp plan knocked the plan out
        # of the executor ("one plan at a time") and the first live T2
        # episode closed never_grasped
        self.toured = False

    def _reset_tour(self) -> None:
        self.candidates = None
        self.tour_idx = 0
        self.tour_meta = None
        self.awaiting = None
        self.read_attempt = 0  # refusal retries spent on this candidate
        self.attempt_offset = 0  # ladder entry the next read_move starts at
        self.last_attempt_used = 0

    def on_goal(self, goal: dict, goal_id: str) -> list:
        if self.goal is not None:  # TC-7: actions do not overlap
            return []
        self.goal, self.goal_id, self.violations = goal, goal_id, {}
        self.ticks = 0
        self._reset_tour()
        self.toured = False
        return [("target_request", {"target_med": goal["target_med"]}, {"goal_id": goal_id})]

    def on_tick(self) -> list:
        """Feedback t = 1 Hz ticks since the goal (CON-5: deterministic —
        a wall-clock read would make same-seed runs emit different
        payloads, and would span episodes rather than the current one)."""
        if self.goal is None:
            return []
        self.ticks += 1
        phase = "scanning" if self.candidates is not None else "executing"
        feedback: dict = {"t": self.ticks, "phase": phase}
        if self.violations:
            feedback["violations"] = dict(self.violations)
        return [("episode_feedback", feedback, {"goal_id": self.goal_id})]

    def on_result(self) -> list:
        self.goal = None
        self._reset_tour()
        self.toured = False
        return []

    def on_violation(self, violation: dict) -> None:
        reason = violation.get("reason", "unknown")
        self.violations[reason] = self.violations.get(reason, 0) + 1

    # -- T2 scan tour ---------------------------------------------------

    def on_target_pose(self, pos: list, metadata: dict, neighbours: list) -> list:
        """Perception answered. T2: start the tour over every box
        position; other tiers never see this input (graph wiring)."""
        if self.tier != READ_TIER or self.goal is None or self.candidates is not None:
            return []
        if self.toured:
            return []  # one tour per goal (see __init__ note)
        self.toured = True
        target_sx = float(self.goal["target_sx"]) if "target_sx" in self.goal else None
        # neighbour rows are same-level (x, y) centres (TC-9 payload);
        # they inherit the target's z for the face point
        z = float(pos[2])
        rows = [list(pos)[:3]] + [
            [float(r[0]), float(r[1]), z] for r in neighbours if r is not None
        ]
        # face point: the requested med's own x-depth is the best
        # available guess for EVERY candidate — identity is unknown
        # until read, and the read ladder tolerates the ~2 cm spread
        self.candidates = [
            (row, [row[0] - (target_sx or 0.0) / 2.0, row[1], row[2]]) for row in rows
        ]
        self.tour_idx = 0
        self.tour_meta = dict(metadata)
        return self._emit_read_move()

    def _emit_read_move(self) -> list:
        assert self.candidates is not None
        if self.tour_idx >= len(self.candidates):
            self._reset_tour()  # exhausted: idle; the episode times out honestly
            return []
        request_id = f"{self.goal_id}/read{self.tour_idx}.{self.read_attempt}"
        self.awaiting = request_id
        _, face = self.candidates[self.tour_idx]
        request = {"face": face}
        if self.attempt_offset:
            request["attempt_offset"] = self.attempt_offset
        return [("read_move", request, {"request_id": request_id})]

    def _advance_candidate(self) -> list:
        self.tour_idx += 1
        self.read_attempt = 0
        self.attempt_offset = 0
        return self._emit_read_move()

    def on_move_done(self, payload: dict, request_id: str) -> list:
        if self.candidates is None or request_id != self.awaiting:
            return []
        if not payload.get("ok", False):  # ladder exhausted: skip candidate
            return self._advance_candidate()
        self.last_attempt_used = int(payload.get("attempt_used", 0))
        request: dict = {"range_m": float(payload["range_m"])}
        if payload.get("pitched"):
            request["pitched"] = True  # the reader raises its margin floor
        for key in ("face", "cam_pos", "cam_rot_cv"):
            # the executor's ACHIEVED camera pose: the reader rectifies
            # the face quad through it (first live tour read a neighbour
            # box confidently wrong without it)
            if payload.get(key) is not None:
                request[key] = payload[key]
        return [("read_request", request, {"request_id": request_id})]

    def on_read_result(self, payload: dict, request_id: str) -> list:
        if self.candidates is None or request_id != self.awaiting:
            return []
        if payload.get("label") == self.goal["target_med"]:
            pos, _ = self.candidates[self.tour_idx]
            metadata = dict(self.tour_meta or {})
            metadata["goal_id"] = self.goal_id
            self._reset_tour()
            return [("grasp_target", {"pos": pos}, metadata)]
        if payload.get("label") is None and self.read_attempt + 1 < MAX_READS_PER_CANDIDATE:
            # REFUSED (not misidentified): the view was bad, not the box —
            # retry this candidate from the next ladder entry (a far-side
            # box reads only from the pitched rungs)
            self.read_attempt += 1
            self.attempt_offset = self.last_attempt_used + 1
            return self._emit_read_move()
        # a DIFFERENT med (or refusals exhausted): keep touring
        return self._advance_candidate()


def main() -> None:
    import json
    import os
    import sys

    import numpy as np
    import pyarrow as pa
    from dora import Node

    from aisle.topics import make_sender

    tier = os.environ.get("AISLE_TASK_TIER", "T1")
    if tier not in ("T1", READ_TIER):
        raise ValueError(f"AISLE_TASK_TIER must be T1 or {READ_TIER}, got {tier!r}")
    target_sx = None
    if tier == READ_TIER:
        from aisle.scenes.pharmacy import load_meds

        meds = load_meds()
        target_sx = {name: float(spec["size"][0]) for name, spec in meds.items()}

    node = Node()
    send = make_sender(node)
    machine = TaskStateMachine(tier=tier)

    def emit(emissions) -> None:
        for topic, payload, metadata in emissions:
            if topic == "grasp_target":
                send(
                    topic,
                    pa.array(np.asarray(payload["pos"] + [0.0, 0.0, 0.0, 1.0], dtype=np.float32)),
                    metadata,
                )
            else:
                send(topic, pa.array([json.dumps(payload)]), metadata)

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            if target_sx is not None and goal.get("target_med") in target_sx:
                goal["target_sx"] = target_sx[goal["target_med"]]
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
        elif event["id"] == "target_pose":
            pos = (
                np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.float64)
                .reshape(-1)[:3]
                .tolist()
            )
            neighbours = json.loads(metadata.get("neighbours", "[]"))
            emit(machine.on_target_pose(pos, metadata, neighbours))
        elif event["id"] == "move_done":
            emit(
                machine.on_move_done(
                    json.loads(event["value"][0].as_py()), metadata.get("request_id", "")
                )
            )
        elif event["id"] == "read_result":
            emit(
                machine.on_read_result(
                    json.loads(event["value"][0].as_py()), metadata.get("request_id", "")
                )
            )


if __name__ == "__main__":
    main()
