"""Behavioral-reset runtime (SPEC 040 RST-2) — the dora-facing attempt
driver the service delegates to.

Holds the latest realistic inputs (overhead rgb/depth, calibration,
joint_state), runs the attempt lifecycle from behavioral.py with the
REAL motion from motion.py, and streams commands on joint_state events.
Commands leave as `reset_joint_cmd`/`reset_gripper_cmd` and pass the
budget guard like every other motion source (VAL-5) — the reset has no
private channel to the arm.

The return slot is sampled deterministically from (request seed,
attempt): sample_placements with the LARGEST med footprint, so any med
fits the slot with the standard separation margins. The other shelf
boxes are wherever the episode left them (behavioral parity — nothing
else teleports); a slot that lands the box against a neighbour fails
the realistic verification and the next attempt samples a fresh slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from aisle.reset.behavioral import MAX_ATTEMPTS
from aisle.reset.motion import (
    ResetStreamer,
    locate_box_in_tray,
    place_back_stages,
    placement_verified,
)

# phases: idle -> moving -> verifying -> (idle | next attempt | fallback)
IDLE, MOVING = "idle", "moving"


def sampled_slot(seed: int, attempt: int, layout: dict, med_names: list[str], meds: dict):
    """Deterministic return slot for (seed, attempt): the largest med's
    footprint stands in for the unknown identity, so whatever box the
    tray held fits with standard margins."""
    from aisle.scenes.pharmacy import sample_placements

    largest = max(med_names, key=lambda n: float(meds[n]["size"][0]) * float(meds[n]["size"][1]))
    placement = None
    for p in sample_placements((seed * 31 + attempt) & 0x7FFFFFFF, [largest], layout):
        placement = p
    if placement is None:
        return None
    return np.array([placement.x, placement.y, placement.z])


@dataclass(kw_only=True)
class BehavioralRuntime:
    """One active behavioral request at a time (TC-6: the reset is a
    service; the client never overlaps requests)."""

    layout: dict
    meds: dict
    home_q: np.ndarray
    model_pair: object = None
    calibration: dict | None = None
    latest_rgb: np.ndarray | None = None
    latest_depth: np.ndarray | None = None
    phase: str = field(default=IDLE)
    # None while running; "success" (box verified on the shelf, no
    # teleport needed) or "exhausted" (caller falls back to teleport)
    outcome: str | None = field(default=None)
    seed: int = field(default=0)
    request_meta: dict = field(default_factory=dict)
    attempts: int = field(default=0)
    streamer: ResetStreamer | None = field(default=None)
    place_pos: np.ndarray | None = field(default=None)

    @property
    def active(self) -> bool:
        return self.phase != IDLE

    def on_bridge_info(self, info: dict) -> None:
        self.calibration = info["calibration"]

    def on_rgb(self, rgb: np.ndarray) -> None:
        self.latest_rgb = rgb

    def on_depth(self, depth: np.ndarray) -> None:
        self.latest_depth = depth

    def start(self, seed: int, request_meta: dict) -> None:
        self.seed = seed
        self.request_meta = dict(request_meta)
        self.attempts = 0
        self.outcome = None
        self.phase = MOVING
        self._begin_attempt()

    def _tray_cfg(self) -> dict:
        return self.layout["tray"]

    def _begin_attempt(self) -> None:
        """Locate + plan; an unplannable attempt burns the attempt and
        tries again immediately (bounded by MAX_ATTEMPTS)."""
        while self.attempts < MAX_ATTEMPTS:
            self.attempts += 1
            self.streamer = None
            if self.latest_rgb is None or self.latest_depth is None or self.calibration is None:
                continue  # no frames yet: attempt unusable
            tray_cfg = self._tray_cfg()
            top = locate_box_in_tray(
                self.latest_rgb,
                self.latest_depth,
                self.calibration,
                list(self.meds),
                tray_cfg,
                model_pair=self.model_pair,
            )
            if top is None:
                continue
            place = sampled_slot(self.seed, self.attempts, self.layout, list(self.meds), self.meds)
            if place is None:
                continue
            shelf = self.layout["shelf"]
            board_z = shelf["pos"][2] + shelf["level_heights"][0] + shelf["board_thickness"] / 2
            tray_top = tray_cfg["pos"][2] + tray_cfg["size"][2] / 2
            stages = place_back_stages(top, tray_top, place, board_z, self.home_q)
            if stages is None:
                continue
            self.place_pos = place
            self.streamer = ResetStreamer(stages=stages)
            return
        # exhausted without a plannable attempt
        self.phase = IDLE
        self.outcome = "exhausted"

    def on_joint_state(self, qpos: np.ndarray) -> tuple[np.ndarray | None, float | None]:
        """Stream the active attempt; when the plan finishes, verify
        realistically and either settle (success), start the next
        attempt, or exhaust. Returns (joint command, gripper value) to
        emit."""
        if self.phase != MOVING or self.streamer is None:
            return None, None
        cmd, grip = self.streamer.step(qpos)
        if not self.streamer.done:
            return cmd, grip
        # plan finished: verify on the FRESHEST frames
        verified = (
            self.latest_rgb is not None
            and self.latest_depth is not None
            and self.place_pos is not None
            and placement_verified(
                self.latest_rgb,
                self.latest_depth,
                self.calibration,
                list(self.meds),
                self.place_pos,
                model_pair=self.model_pair,
            )
        )
        if verified:
            self.phase = IDLE
            self.outcome = "success"
            return None, None
        if self.attempts < MAX_ATTEMPTS:
            self._begin_attempt()  # sets exhausted itself when out of budget
            return None, None
        self.phase = IDLE
        self.outcome = "exhausted"
        return None, None
