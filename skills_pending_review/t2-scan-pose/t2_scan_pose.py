"""t2-scan-pose (agent-authored, campaign arm-L T2): candidate positions
for the T2 scan tour WITHOUT the identity gate.

Root cause measured on run 20260813-162725-2be572 (seeds 0..9, pass1
0.10): l2_pose refuses target_pose when the TARGET's color-derived
identity margin is low ("identity margin -0.080 under the 0.01 floor",
150+ frames per episode) or when the detected box fails the upright
footprint check against the TARGET's size — but at T2
AISLE_SHUFFLE_COLORS makes detection identity noise BY DESIGN
(expert_t2.yaml: "identity claims are color-derived noise at T2 — only
the POSITIONS are trusted"). Five of ten episodes never started their
tour and closed never_grasped with zero reads.

This node keeps the review-hardened FramePairSession lifecycle and the
depth-backprojection geometry, and drops identity from the contract:

* anchor (the target_pose payload) = highest-score confident detection,
  ANY label — no identity margin gate, no target-footprint gate;
* tour_candidates (new metadata key, consumed by t2-scan-tsm) = every
  other confident detection's full [x, y, z] centre, deduped at 3 cm
  (l2_pose's neighbours carry [x, y] and the state machine inherited
  the anchor's z — but sample_placements puts boxes on DIFFERENT shelf
  levels, so a cross-level candidate was toured at the anchor's level
  and read blank: ep2/ep4 scored ~0.03 on every hypothesis);
* neighbours stays the PLANNER-compatible positional per-med payload
  (grasp_topdown zips MED_NAMES strict=True for fingertip clearance),
  score-floored exactly as l2_pose built it;
* each candidate's centre z still comes from its own measured top
  surface minus half the DETECTED label's height — a +-1 cm guess the
  reader's board-top snap and the promotion z-snap absorb.

Safety: identity comes ONLY from the label reader's margin-floored read
(wrong-med protection unchanged); a box position is not an identity
claim. Frames with NO confident detection still refuse and retry.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aisle.nodes.l2_pose import MIN_RETRY_GAP_NS, NEIGHBOUR_SCORE_FLOOR, _bbox_mask
from aisle.nodes.perception_session import FramePairSession
from aisle.nodes.segmented_pose import PoseRefused, estimate_pose

# two detections whose back-projected centres land within this xy radius
# are one box seen by two color queries — keep the higher score
DEDUP_RADIUS_M = 0.03


@dataclass(kw_only=True)
class ScanPoseSession(FramePairSession):
    """All confident box positions, ranked by detection score.

    `detector(rgb) -> [{label, score, box}]` and `backprojector` are
    injected as in L2Session so the rules stay unit-testable.
    """

    detector: object = None
    backprojector: object = None
    retry_gap_ns: int = MIN_RETRY_GAP_NS
    last_attempt_ns: int = -(10**18)

    # the observation frame is the rendered rgb (L2 parity)
    on_rgb = FramePairSession.on_obs

    def on_reset_done(self) -> None:
        super().on_reset_done()
        # sim time is monotonic across teleports (l2_pose parity)
        self.last_attempt_ns = -(10**18)

    def _estimate(self, rgb: np.ndarray, depth: np.ndarray) -> dict | None:
        stamp = self.latest_depth[0]
        if stamp - self.last_attempt_ns < self.retry_gap_ns:
            return None  # throttled: not an attempt, request stays pending
        self.last_attempt_ns = stamp
        detections = [
            d
            for d in self.detector(rgb)
            if d["score"] >= NEIGHBOUR_SCORE_FLOOR and all(np.isfinite(v) for v in d["box"])
        ]
        if not detections:
            raise PoseRefused("no confident box detection in frame (scan mode)")
        project = self.backprojector(self.calibration)
        kept: list[tuple[float, list, dict]] = []  # (score, pos, estimate)
        refused = 0
        for det in sorted(detections, key=lambda d: -d["score"]):
            # the detected label is identity NOISE at T2; its height is
            # still the best available guess for this box's extent
            height = float(self.meds[det["label"]]["size"][2])
            try:
                est = estimate_pose(
                    _bbox_mask(depth.shape, det["box"]), depth, [1], height, project
                )
            except PoseRefused:
                refused += 1
                continue
            pos = [float(v) for v in est["pos"]]
            if any(
                (pos[0] - p[0]) ** 2 + (pos[1] - p[1]) ** 2 < DEDUP_RADIUS_M**2 for _, p, _ in kept
            ):
                continue  # same box through a second color query
            kept.append((float(det["score"]), pos, est))
        if not kept:
            raise PoseRefused("every confident detection failed depth back-projection")
        # planner-compatible POSITIONAL slots (l2_pose parity: one row per
        # med in manifest order, grasp_topdown zips MED_NAMES strict=True).
        # Score floor only — a mislabeled but CONFIDENT box still marks a
        # real obstacle for fingertip clearance.
        slots: list[list[float] | None] = []
        for name, spec in self.meds.items():
            cands = [d for d in detections if d["label"] == name]
            if not cands:
                slots.append(None)
                continue
            box = max(cands, key=lambda d: d["score"])["box"]
            try:
                n_est = estimate_pose(
                    _bbox_mask(depth.shape, box), depth, [1], float(spec["size"][2]), project
                )
            except PoseRefused:
                slots.append(None)
                continue
            slots.append([float(n_est["pos"][0]), float(n_est["pos"][1])])
        score, pos, est = kept[0]
        return {
            "pos": pos,
            "target_med": self.target,
            "neighbours": slots,
            "tour_candidates": [p for _, p, _ in kept[1:]],
            "neighbours_refused": refused,
            "mask_pixels": est["mask_pixels"],
            "detection": {"score": score, "margin": 0.0, "box": []},
        }


def main() -> None:  # pragma: no cover — dora runtime
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import MED_NAMES, load_meds
    from aisle.topics import make_sender
    from aisle.verifier.models import detect_meds, load_pinned
    from aisle.verifier.stages import backproject_overhead

    model_pair = load_pinned("identity")
    node = Node()
    send = make_sender(node)
    session = ScanPoseSession(
        meds=load_meds(),
        detector=lambda rgb: detect_meds(rgb, MED_NAMES, model_pair=model_pair),
        backprojector=lambda calibration: (
            lambda depth, pixels: backproject_overhead(depth, calibration, pixels)
        ),
    )

    for event in node:
        if event["type"] != "INPUT":
            continue
        topic, metadata = event["id"], (event.get("metadata") or {})
        if topic == "bridge_info":
            session.on_bridge_info(json.loads(event["value"][0].as_py()))
        elif topic == "target_request":
            request = json.loads(event["value"][0].as_py())
            if not session.on_target_request(request):
                print(
                    f"target_request refused: unknown med {request.get('target_med')!r}",
                    file=sys.stderr,
                )
        elif topic == "reset_done":
            session.on_reset_done()
        elif topic in ("depth_overhead", "rgb_overhead"):
            h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
            if h <= 0 or w <= 0:
                print(f"{topic} frame skipped: h={h} w={w}", file=sys.stderr)
                continue
            stamp = int(metadata.get("sim_time_ns", -1))
            frame = np.asarray(event["value"].to_numpy(zero_copy_only=False))
            try:
                if topic == "depth_overhead":
                    out = session.on_depth(stamp, frame.astype(np.float32).reshape(h, w))
                else:
                    out = session.on_rgb(stamp, frame.astype(np.uint8).reshape(h, w, 3))
            except PoseRefused as exc:
                print(f"scan pose refused: {exc}", file=sys.stderr)
                continue
            if out is None:
                continue
            send(
                "target_pose",
                pa.array(np.asarray(out["pos"] + [0.0, 0.0, 0.0, 1.0], dtype=np.float32)),
                {
                    **{k: v for k, v in metadata.items() if k not in ("enc", "h", "w")},
                    "target_med": out["target_med"],
                    "neighbours": json.dumps(out["neighbours"]),
                    "tour_candidates": json.dumps(out["tour_candidates"]),
                    "neighbours_refused": out["neighbours_refused"],
                    "mask_pixels": out["mask_pixels"],
                    "detection": json.dumps(out["detection"]),
                    "perception": "L2",
                },
            )


if __name__ == "__main__":  # pragma: no cover
    main()
