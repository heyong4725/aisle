"""t2-scan-tsm (agent-authored; ported to the ADR-30/HEAD API for the
t2_breakthrough campaign): the T2 scan-tour state machine with
per-candidate z, spatial tour order, confirm gate, blank skip, and tour
restarts — the arm-L T2 campaign's measured-best configuration.

Changes over aisle.nodes.task_state_machine (all evidence from arm-L
runs; see the staged original in skills_pending_review/t2-scan-tsm):

* candidate rows may carry their OWN z ([x, y, z] tour_candidates from
  t2-scan-pose); boxes sit on different shelf levels and z-inheritance
  read cross-level candidates blank.
* the promoted grasp_target's z snaps to board geometry with the
  IDENTIFIED med's true height; the planner's positional neighbours
  payload is re-aimed at the box actually being grasped.
* a matched target read under CONFIRM_MARGIN promotes only after a
  confirming re-read from the next ladder rung agrees (wrong-med = 10x).
* a refused read with no absolute signal (best score < BLANK_SCORE)
  skips the candidate instead of burning a retry transit.
* a tour that exhausts with no match re-requests a fresh estimate
  (MAX_TOUR_RESTARTS) instead of idling to the verifier timeout.
* ELIMINATION fallback (r3): one box per med — when the goal's 4
  NON-target meds have all been read confidently at 4 distinct
  positions (accumulated across tours; confident reads are
  deterministic template matches and boxes do not move until grasped)
  and exactly one distinct candidate position remains unlabeled, that
  position IS the target. Promoting it trusts only the confident
  non-target reads, never a refused read — the wrong-med (10x) path
  would require a confident MISread of a non-target, which the margin
  floor exists to prevent.

The v7 pitched-first ladder heuristic moved OUT of this node into the
t2-read-ladder skill, where the ladder actually lives — this port drops
the attempt-offset reach-in, which indexed a ladder order that skill
changes.

Port deltas for HEAD: aisle.turn_node.Node, lockstep turn-driven ticks,
on_read_result's event_metadata arg, and TURN_STAMP_KEYS hygiene in
tour_meta (retain semantic annotations, not the transport stamp).
"""

from __future__ import annotations

from aisle.nodes.task_state_machine import READ_TIER, TURN_STAMP_KEYS, TaskStateMachine


class ScanTourMachine(TaskStateMachine):
    # per candidate: first read + one refusal retry from the next ladder
    # rung (refused margins 0.025-0.036 against the 0.04 floor with
    # strong absolute scores ~0.5 are common; a second view is cheap)
    MAX_READS_PER_CANDIDATE = 2
    # a tour that exhausts with no match re-requests a fresh estimate
    MAX_TOUR_RESTARTS = 2
    # a matched target read below this margin promotes only after a
    # CONFIRMING re-read from the next ladder rung agrees (run 74fce0
    # ep3: two boxes both read "ibuprofen", one at margin 0.042)
    CONFIRM_MARGIN = 0.10
    # a refused read whose BEST absolute score is under this carries no
    # label signal at all — skip the candidate, don't retry the view
    BLANK_SCORE = 0.15
    # two candidate rows within this xy radius are the same physical box
    # (dedupe scale, matches SLOT_MATCH_RADIUS_M)
    ELIM_MATCH_RADIUS_M = 0.05
    # a PITCHED refusal with at least this margin has real label signal
    # the pitched floor (0.15) is rejecting; re-read from the ladder's
    # flat rungs where the 0.04 floor is the trustworthy one (wrong flat
    # reads measured <= 0.036, wrong pitched reads up to 0.14)
    FLAT_RETRY_MARGIN = 0.05

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
        # (xy, label) confident-read evidence, accumulated ACROSS tour
        # restarts within one goal (positions are stable; estimates jitter
        # under ELIM_MATCH_RADIUS_M)
        self.label_map: list[tuple[list, str]] = []

    def on_goal(self, goal: dict, goal_id: str) -> list:
        emissions = super().on_goal(goal, goal_id)
        if emissions:
            self.tour_restarts = 0
            self.label_map = []
        return emissions

    def _reset_tour(self) -> None:
        super()._reset_tour()
        self.confirming = False
        self.flat_retry = False
        self._last_park_pitched = False
        self._flat_confirm_used = False

    def on_move_done(self, payload: dict, request_id: str) -> list:
        if self.candidates is not None and request_id == self.awaiting and payload.get("ok", False):
            self._last_park_pitched = bool(payload.get("pitched"))
        return super().on_move_done(payload, request_id)

    def _advance_candidate(self) -> list:
        # a confirm whose park failed must not leak onto the next
        # candidate's first read (it would skip the confirm gate)
        self.confirming = False
        self.flat_retry = False
        self._flat_confirm_used = False
        return super()._advance_candidate()

    def on_result(self) -> list:
        self.tour_restarts = 0
        self.label_map = []
        return super().on_result()

    # face-aim raise for LADDER-EMPTY faces only (measured: raising every
    # low face moves the aim on measured-good candidates and flips
    # successes; a face under 2 cm clearance has NO solvable entry at all)
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

    def _emit_read_move(self) -> list:
        assert self.candidates is not None
        if self.tour_idx >= len(self.candidates) and self.goal is not None:
            promoted = self._try_elimination()
            if promoted is not None:
                return promoted
            if self.tour_restarts < self.MAX_TOUR_RESTARTS:
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
        flat_retry, self.flat_retry = getattr(self, "flat_retry", False), False
        if flat_retry:
            self.attempt_offset = 0  # the flat list starts fresh in the executor
        emissions = super()._emit_read_move()
        if flat_retry and emissions and emissions[0][0] == "read_move":
            emissions[0][1]["flat_only"] = True
        return emissions

    def _try_elimination(self) -> list | None:
        """On tour exhaustion: if the 4 non-target meds were all read
        confidently at 4 distinct positions and exactly ONE distinct
        candidate position carries no label, it is the target (one box
        per med). Any ambiguity — a duplicated label, a confident target
        read that somehow did not promote, 0 or 2+ unlabeled positions —
        refuses. Never trusts a refused read."""
        if self.goal is None or not self.candidates or not self.med_heights:
            return None
        target = self.goal["target_med"]
        if target not in self.med_heights:
            return None
        labels = [label for _, label in self.label_map]
        if target in labels or len(labels) != len(set(labels)):
            return None
        if set(labels) != set(self.med_heights) - {target}:
            return None
        r2 = self.ELIM_MATCH_RADIUS_M**2
        labeled_xy = [xy for xy, _ in self.label_map]
        unresolved: list[list] = []
        for row, _ in self.candidates:
            if any((row[0] - p[0]) ** 2 + (row[1] - p[1]) ** 2 < r2 for p in labeled_xy):
                continue  # the same box as a confidently-labeled one
            if any((row[0] - q[0]) ** 2 + (row[1] - q[1]) ** 2 < r2 for q in unresolved):
                continue  # a duplicate row of an unresolved box
            unresolved.append(row)
        if len(unresolved) != 1:
            return None
        import sys

        print(
            f"elimination promote: 4 non-targets read, sole unresolved is {target}",
            file=sys.stderr,
        )
        return self._promote(list(unresolved[0]), None)

    def _promote(self, pos: list, event_metadata: dict | None) -> list:
        """Board-geometry z snap, planner-slot re-aim, tour reset — the
        single promotion path for matched reads and elimination."""
        sz = self.med_heights.get(self.goal["target_med"])
        if self.board_tops and sz is not None:
            top = min(self.board_tops, key=lambda t: abs(t + sz / 2.0 - pos[2]))
            pos = [pos[0], pos[1], top + sz / 2.0]
        metadata = dict(self.tour_meta or {})
        if event_metadata is not None:
            metadata.update(
                {key: event_metadata[key] for key in TURN_STAMP_KEYS if key in event_metadata}
            )
        metadata["goal_id"] = self.goal_id
        self._reaim_planner_slots(metadata, pos)
        self._reset_tour()
        return [("grasp_target", {"pos": pos}, metadata)]

    def on_target_pose(self, pos: list, metadata: dict, neighbours: list) -> list:
        """Parent logic verbatim, except a 3-element neighbour row keeps
        its own z instead of inheriting the anchor's, and the tour order
        is spatial (level, then y) instead of score order."""
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
        # SPATIAL tour order: same level then adjacent y keeps each hop
        # short and level-local. BOTTOM level first (measured both ways:
        # top-first doubled collisions, every one on the home->high
        # first transit).
        rows.sort(key=lambda row: (round(row[2], 1), row[1]))
        self.candidates = [
            (row, [row[0] - (target_sx or 0.0) / 2.0, row[1], row[2]]) for row in rows
        ]
        self._raise_low_faces()
        self.tour_idx = 0
        self.tour_meta = {
            key: value for key, value in metadata.items() if key not in TURN_STAMP_KEYS
        }
        return self._emit_read_move()

    def on_read_result(
        self, payload: dict, request_id: str, event_metadata: dict | None = None
    ) -> list:
        """Parent logic plus the confirm gate, blank skip, board-geometry
        z snap, and planner-slot re-aim on promotion."""
        if self.candidates is None or request_id != self.awaiting:
            return []
        confirming, self.confirming = getattr(self, "confirming", False), False
        label = payload.get("label")
        if (
            label is not None
            and label != self.goal["target_med"]
            and self.tour_idx < len(self.candidates)
        ):
            # confident non-target read: elimination evidence, keyed by
            # position (upsert within the dedupe radius; a re-read of the
            # same box refreshes rather than duplicates)
            row = self.candidates[self.tour_idx][0]
            r2 = self.ELIM_MATCH_RADIUS_M**2
            self.label_map = [
                (xy, lb)
                for xy, lb in self.label_map
                if (xy[0] - row[0]) ** 2 + (xy[1] - row[1]) ** 2 >= r2
            ]
            self.label_map.append((list(row[:2]), label))
        if label == self.goal["target_med"]:
            if float(payload.get("margin", 0.0)) < self.CONFIRM_MARGIN and not confirming:
                # bare-floor match: a wrong promotion costs 10x — require
                # a second ladder rung to agree before grasping
                self.confirming = True
                self.read_attempt += 1
                self.attempt_offset = self.last_attempt_used + 1
                return self._emit_read_move()
            pos, _ = self.candidates[self.tour_idx]
            return self._promote(list(pos), event_metadata)
        if confirming:
            if (
                label is None
                and not getattr(self, "_flat_confirm_used", False)
                and getattr(self, "_last_park_pitched", False)
                and float(payload.get("margin", 0.0)) >= self.FLAT_RETRY_MARGIN
            ):
                # the confirm read was a PITCHED refusal with signal: one
                # flat re-confirm before abandoning — the flat floor is
                # the trustworthy one; a flat target read promotes,
                # anything else advances
                self._flat_confirm_used = True
                self.confirming = True
                self.read_attempt += 1
                self.flat_retry = True
                return self._emit_read_move()
            # the confirm read disagreed (refused or another med): the
            # bare-floor match was untrustworthy — never grasp on it
            return self._advance_candidate()
        scores = payload.get("scores") or {}
        best_abs = max(scores.values(), default=0.0)
        if label is None and self.read_attempt + 1 < self.MAX_READS_PER_CANDIDATE:
            if (
                getattr(self, "_last_park_pitched", False)
                and float(payload.get("margin", 0.0)) >= self.FLAT_RETRY_MARGIN
            ):
                # PITCHED refusal with real signal: the pitched floor
                # rejects margins a flat read would accept — NEVER lower
                # the floor; re-read from the ladder's flat rungs instead
                self.read_attempt += 1
                self.flat_retry = True
                return self._emit_read_move()
            if best_abs >= self.BLANK_SCORE:
                # REFUSED with signal in view (not misidentified, not
                # blank): retry this candidate from the next ladder rung
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

    from aisle.nodes.label_reader import shelf_board_tops
    from aisle.scenes.pharmacy import load_meds, load_physics, resolve_layout
    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

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
    lockstep = os.environ.get("AISLE_LOCKSTEP", "0").strip().lower() in ("1", "true", "yes")
    last_tick_sim_ns = -1

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
            if lockstep:
                last_tick_sim_ns = int(metadata.get("sim_time_ns", 0))
        elif event["id"] == "episode_result":
            emit(machine.on_result())
        elif event["id"] == "violation":
            machine.on_violation(json.loads(event["value"][0].as_py()))
        elif event["id"] == "tick" and not lockstep:
            emit(machine.on_tick())
        elif event["id"] == "turn" and lockstep:
            sim_time_ns = int(metadata.get("sim_time_ns", 0))
            if last_tick_sim_ns < 0:
                last_tick_sim_ns = sim_time_ns
            while sim_time_ns - last_tick_sim_ns >= 1_000_000_000:
                last_tick_sim_ns += 1_000_000_000
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
                    json.loads(event["value"][0].as_py()),
                    metadata.get("request_id", ""),
                    metadata,
                )
            )


if __name__ == "__main__":  # pragma: no cover
    main()
