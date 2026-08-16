"""t2-scan-tsm (agent-authored, campaign arm-L T2): the T2 scan-tour
state machine, adapted to per-candidate z from t2-scan-pose.

Two surgical changes over aisle.nodes.task_state_machine (run
20260813-162725-2be572 evidence):

* candidate rows may carry their OWN z ([x, y, z] neighbours from
  t2-scan-pose). The stock machine forced every neighbour to the
  anchor's z, but sample_placements puts boxes on DIFFERENT shelf
  levels — a cross-level candidate was toured at the anchor's level and
  every hypothesis read blank (~0.03 NCC, ep2/ep4). Two-element rows
  keep the old inherit-anchor-z behavior.

* the promoted grasp_target's z is snapped to board geometry using the
  IDENTIFIED med's true height (board_top + sz/2 — the same public-
  layout snap the label reader applies to its hypotheses). Until the
  read, every candidate's z rides a detected-label height GUESS
  (identity is noise at T2); after the read the true height is known,
  so the guess need not reach the grasp planner.

* at promotion the planner's positional `neighbours` metadata is
  re-aimed: grasp_topdown excludes the slot NAMED target_med, but with
  shuffled colors that name points at the wrong box — the slot nearest
  the promoted position is swapped into the target_med slot so the
  planner excludes the box it is actually grasping and keeps the other
  four as fingertip-clearance constraints.

Everything else — tour order, one-read-per-candidate, refusal
advancement, HAR-3 retry arming, feedback cadence — is inherited
unchanged. Wrong-med protection stays in the label reader's margin
floors; this node never promotes without a matching read.
"""

from __future__ import annotations

from aisle.nodes.task_state_machine import READ_TIER, TaskStateMachine


class ScanTourMachine(TaskStateMachine):
    # per candidate: first read + one refusal retry from the next ladder
    # entry (stock pins 1: run ac0942 refused reads at margins
    # 0.025-0.036 against the 0.04 floor with STRONG absolute scores
    # ~0.5 — a second view from a different rung is cheap relative to a
    # candidate lost for the episode, and the retry transit is a local
    # retreat+park on the SAME face, not a home->shelf sweep)
    MAX_READS_PER_CANDIDATE = 2
    # a tour that exhausts with no match re-requests a fresh estimate
    # instead of idling to the verifier timeout (baseline ep5: all
    # candidates refused, then 60 idle seconds; timeout and a failed
    # re-tour are the same 1x failure class, so the retry is free EV)
    MAX_TOUR_RESTARTS = 2
    # a matched target read below this margin promotes only after a
    # CONFIRMING re-read from the next ladder entry agrees (run 74fce0
    # ep3: two different boxes both read "ibuprofen", one at margin
    # 0.042 a hair over the 0.04 floor — at least one was wrong, and had
    # ibuprofen been the target that bare-floor read was a 10x
    # wrong_object away; the true-positive population reads 0.10-0.60)
    CONFIRM_MARGIN = 0.10
    # a refused read whose BEST absolute score is under this carries no
    # label signal at all (run 74fce0 ep9: phantom candidates read max
    # 0.028 twice) — skip the candidate instead of burning a retry
    # transit on an empty view
    BLANK_SCORE = 0.15

    def __init__(
        self,
        *,
        board_tops: tuple[float, ...] | None = None,
        med_heights: dict[str, float] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.board_tops = board_tops
        self.med_heights = med_heights or {}
        self.tour_restarts = 0

    def on_goal(self, goal: dict, goal_id: str) -> list:
        emissions = super().on_goal(goal, goal_id)
        if emissions:
            self.tour_restarts = 0
        return emissions

    def _reset_tour(self) -> None:
        super()._reset_tour()
        self.confirming = False

    def _advance_candidate(self) -> list:
        # a confirm whose park failed must not leak onto the next
        # candidate's first read (it would skip the confirm gate)
        self.confirming = False
        return super()._advance_candidate()

    def on_result(self) -> list:
        self.tour_restarts = 0
        return super().on_result()

    # face-aim raise for LADDER-EMPTY faces only. Measured both ways:
    # run 7b78e9 found a face 1.3 cm above its board with NO solvable
    # ladder entry (seed 6's target silently skipped both tours), but
    # run c8708f showed that raising every low face (5.5/3.0 cm
    # thresholds) moves the aim on measured-good candidates and flipped
    # three prior successes — so only faces under RAISE_BELOW_M (which
    # the solver cannot serve at all) are raised, to RAISE_TO_M, a
    # clearance the same run's upper-board reads tracked clean at.
    RAISE_BELOW_M = 0.020
    RAISE_TO_M = 0.030

    def _raise_low_faces(self) -> None:
        if not self.board_tops or not self.candidates:
            return
        lowest = min(self.board_tops)
        for _, face in self.candidates:
            below = [t for t in self.board_tops if t <= face[2] + 0.01]
            board = max(below) if below else lowest
            if face[2] - board < self.RAISE_BELOW_M:
                face[2] = board + self.RAISE_TO_M

    # lowest-board faces under this clearance jam on the flat close
    # ladder entries (run 73d6d1: 56 of 115 parks bailed, 93/115 at
    # z<=0.11): start their ladder at the PITCHED rungs (entry index 3,
    # camera descends from above the board — no jam path) WITHOUT moving
    # the aim (run c8708f showed aim shifts flip good candidates). Only
    # for near-side faces: ik already orders far-side (+y) pitched-first.
    PITCH_FIRST_CLEARANCE_M = 0.05
    PITCHED_LADDER_OFFSET = 3
    FAR_SIDE_Y = 0.05  # ik_trajectory.FAR_SIDE_Y

    def _emit_read_move(self) -> list:
        assert self.candidates is not None
        if self.tour_idx < len(self.candidates) and self.attempt_offset == 0 and self.board_tops:
            _, face = self.candidates[self.tour_idx]
            lowest = min(self.board_tops)
            if (
                face[1] <= self.FAR_SIDE_Y
                and face[2] - lowest < self.PITCH_FIRST_CLEARANCE_M
                and face[2] >= lowest - 0.01
            ):
                self.attempt_offset = self.PITCHED_LADDER_OFFSET
        if (
            self.tour_idx >= len(self.candidates)
            and self.goal is not None
            and self.tour_restarts < self.MAX_TOUR_RESTARTS
        ):
            self.tour_restarts += 1
            self._reset_tour()
            self.toured = False  # a fresh estimate starts a fresh tour
            return [
                (
                    "target_request",
                    {"target_med": self.goal["target_med"]},
                    {"goal_id": self.goal_id},
                )
            ]
        return super()._emit_read_move()

    def on_target_pose(self, pos: list, metadata: dict, neighbours: list) -> list:
        """Parent logic verbatim, except a 3-element neighbour row keeps
        its own z instead of inheriting the anchor's."""
        if self.tier != READ_TIER or self.goal is None or self.candidates is not None:
            return []
        if self.toured:
            return []  # one tour per goal (parent __init__ note)
        self.toured = True
        target_sx = float(self.goal["target_sx"]) if "target_sx" in self.goal else None
        z = float(pos[2])
        rows = [list(pos)[:3]] + [
            [float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 else z]
            for r in neighbours
            if r is not None
        ]
        if self.candidate_bounds is not None:
            bounds = self.candidate_bounds
            rows = [
                [row[0], row[1], min(max(row[2], bounds["z"][0]), bounds["z"][1])]
                for row in rows
                if bounds["x"][0] <= row[0] <= bounds["x"][1]
                and bounds["y"][0] <= row[1] <= bounds["y"][1]
            ]
        if not rows:
            self.toured = False  # nothing plausible yet: allow a fresh estimate
            return []
        # SPATIAL tour order (run ac0942: both eval failures were contact
        # during between-candidate transits — score order criss-crossed
        # shelf levels and sides). Same level then adjacent y keeps each
        # hop short and level-local; the one cross-level hop happens
        # once. BOTTOM level first, measured both ways: top-first (run
        # 2705b6) doubled collisions to 4, every one on the home->high
        # first transit (t 4.3-17.4 s); bottom-first (run 74fce0) had 2.
        rows.sort(key=lambda row: (round(row[2], 1), row[1]))
        self.candidates = [
            (row, [row[0] - (target_sx or 0.0) / 2.0, row[1], row[2]]) for row in rows
        ]
        self._raise_low_faces()
        self.tour_idx = 0
        self.tour_meta = dict(metadata)
        return self._emit_read_move()

    def on_read_result(self, payload: dict, request_id: str) -> list:
        """Parent logic verbatim, plus the board-geometry z snap and the
        planner-slot re-aim on promotion (the read just told us which box
        the target actually is)."""
        if self.candidates is None or request_id != self.awaiting:
            return []
        confirming, self.confirming = getattr(self, "confirming", False), False
        if payload.get("label") == self.goal["target_med"]:
            if float(payload.get("margin", 0.0)) < self.CONFIRM_MARGIN and not confirming:
                # bare-floor match: a wrong promotion costs 10x — require
                # a second ladder entry to agree before grasping
                self.confirming = True
                self.read_attempt += 1
                self.attempt_offset = self.last_attempt_used + 1
                return self._emit_read_move()
            pos, _ = self.candidates[self.tour_idx]
            sz = self.med_heights.get(self.goal["target_med"])
            if self.board_tops and sz is not None:
                top = min(self.board_tops, key=lambda t: abs(t + sz / 2.0 - pos[2]))
                pos = [pos[0], pos[1], top + sz / 2.0]
            metadata = dict(self.tour_meta or {})
            metadata["goal_id"] = self.goal_id
            self._reaim_planner_slots(metadata, pos)
            self._reset_tour()
            return [("grasp_target", {"pos": pos}, metadata)]
        if confirming:
            # the confirm read disagreed (refused or another med): the
            # bare-floor match was untrustworthy — never grasp on it
            return self._advance_candidate()
        scores = payload.get("scores") or {}
        best_abs = max(scores.values(), default=0.0)
        if (
            payload.get("label") is None
            and best_abs >= self.BLANK_SCORE
            and self.read_attempt + 1 < self.MAX_READS_PER_CANDIDATE
        ):
            # REFUSED with signal in view (not misidentified, not blank):
            # retry this candidate from the next ladder entry
            self.read_attempt += 1
            self.attempt_offset = self.last_attempt_used + 1
            return self._emit_read_move()
        # a DIFFERENT med, a blank view, or refusals exhausted: keep touring
        return self._advance_candidate()

    # a promoted candidate and its planner slot are the same physical box
    # when their xy centres agree within this radius (dedupe-scale)
    SLOT_MATCH_RADIUS_M = 0.05

    def _reaim_planner_slots(self, metadata: dict, pos: list) -> None:
        """grasp_topdown excludes the positional slot NAMED target_med from
        its fingertip-clearance constraints; with shuffled colors that name
        points at the wrong box. Swap the slot nearest the promoted position
        into the target_med slot so the planner excludes the box it grasps
        and keeps the rest as constraints. No-op on any malformed payload —
        the stock (constraint-on-self) behavior is the fallback."""
        import json

        med_names = list(self.med_heights)
        target = self.goal["target_med"]
        if target not in med_names or "neighbours" not in metadata:
            return
        try:
            slots = json.loads(metadata["neighbours"])
        except (TypeError, ValueError):
            return
        if not isinstance(slots, list) or len(slots) != len(med_names):
            return
        best_i, best_d = None, self.SLOT_MATCH_RADIUS_M**2
        for i, row in enumerate(slots):
            if row is None:
                continue
            d = (float(row[0]) - pos[0]) ** 2 + (float(row[1]) - pos[1]) ** 2
            if d < best_d:
                best_i, best_d = i, d
        ti = med_names.index(target)
        if best_i is not None and best_i != ti:
            slots[best_i], slots[ti] = slots[ti], slots[best_i]
        # two color queries can latch the SAME physical box: any other slot
        # still at the promoted position is a phantom constraint ON the
        # grasp target (zero clearance on every grip axis) — drop it
        for i, row in enumerate(slots):
            if i == ti or row is None:
                continue
            if (float(row[0]) - pos[0]) ** 2 + (
                float(row[1]) - pos[1]
            ) ** 2 < self.SLOT_MATCH_RADIUS_M**2:
                slots[i] = None
        metadata["neighbours"] = json.dumps(slots)


def main() -> None:  # pragma: no cover — dora runtime
    import json
    import os
    import sys

    import numpy as np
    import pyarrow as pa
    from dora import Node

    from aisle.nodes.label_reader import shelf_board_tops
    from aisle.scenes.pharmacy import load_meds, load_physics, resolve_layout
    from aisle.topics import env_accepts, env_pin_from_env, make_sender

    tier = os.environ.get("AISLE_TASK_TIER", READ_TIER)
    if tier != READ_TIER:
        raise ValueError(f"t2-scan-tsm is a {READ_TIER}-only node, got AISLE_TASK_TIER={tier!r}")

    meds = load_meds()
    target_sx = {name: float(spec["size"][0]) for name, spec in meds.items()}
    med_heights = {name: float(spec["size"][2]) for name, spec in meds.items()}
    layout = resolve_layout(load_physics(), os.environ.get("AISLE_EMBODIMENT", "franka"))
    shelf = layout["shelf"]
    pad = max(max(float(v) for v in spec["size"]) for spec in meds.values())
    half_x, half_y = shelf["level_size"][0] / 2.0, shelf["level_size"][1] / 2.0
    z_lo = shelf["pos"][2] + shelf["level_heights"][0]
    z_hi = shelf["pos"][2] + shelf["level_heights"][-1] + shelf["board_thickness"] + pad
    candidate_bounds = {
        "x": (shelf["pos"][0] - half_x - pad, shelf["pos"][0] + half_x + pad),
        "y": (shelf["pos"][1] - half_y - pad, shelf["pos"][1] + half_y + pad),
        "z": (z_lo, z_hi),
    }

    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)
    max_retries_raw = os.environ.get("AISLE_MAX_RETRIES", "0").strip()
    if not max_retries_raw.isdigit() or not 0 <= int(max_retries_raw) <= 8:
        raise ValueError(f"AISLE_MAX_RETRIES must be an int in [0, 8], got {max_retries_raw!r}")
    machine = ScanTourMachine(
        tier=tier,
        candidate_bounds=candidate_bounds,
        max_retries=int(max_retries_raw),
        board_tops=shelf_board_tops(),
        med_heights=med_heights,
    )

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
        if not env_accepts(metadata, env_pin):
            continue  # fleet mode (BRG-5): another env's stream
        if event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            if goal.get("target_med") in target_sx:
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
        elif event["id"] == "plan_done":
            machine.on_plan_done()
        elif event["id"] == "target_pose":
            pos = (
                np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.float64)
                .reshape(-1)[:3]
                .tolist()
            )
            # t2-scan-pose ships tour rows (with z) separately from the
            # planner's positional slots; fall back to the stock payload
            neighbours = json.loads(
                metadata.get("tour_candidates", metadata.get("neighbours", "[]"))
            )
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


if __name__ == "__main__":  # pragma: no cover
    main()
