"""L2 perception: pose from DETECTION on rendered rgb (TC-9's top rung).

No ground truth of any kind reaches the graph at L2 — the bridge publishes
neither `poses` nor `seg_overhead`, and this node runs the pinned OWLv2
detector (the frozen verifier's model, imported read-only) on the
`rgb_overhead`/`depth_overhead` pair, then pushes the detection box through
the SAME top-surface geometry as L1's mask (segmented_pose.estimate_pose
over a synthetic bbox mask).

The identity-safety rules are measured, not guessed (idea I7, shelf frames
from run l1-mask-audit-r2): the detector found 19/19 med instances (0%
undetected, 0.7 s/frame CPU) but wrong-id'd 16% — every confusion carried a
score margin <= 0.034 over its overlapping rival while right-ids ran a
median margin of 0.134. Hence:

* MARGIN_FLOOR: a detection whose score does not clear its best OVERLAPPING
  rival by the floor is REFUSED — under the task's asymmetric goal a
  wrong-medicine delivery is 10x worse than a failure to deliver, so a
  low-margin guess is never worth it; the request stays pending and retries
  on a later frame, timing out honestly if the ambiguity persists.
* MIN_RETRY_GAP_NS: detection costs ~0.7 s/frame against a 15 Hz pair
  stream, so refused requests retry at most once per gap of SIM time —
  a persistent refusal must not lag the node's event queue unboundedly.

The frame-pairing lifecycle (once-per-request, reset clears target and
buffers, symmetric same-stamp pairing, unknown-med refusal) is the shared
review-hardened perception_session.FramePairSession.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from aisle.nodes.perception_session import FramePairSession
from aisle.nodes.segmented_pose import PoseRefused, estimate_pose

# measured under the IMPLEMENTED rival rule (round-2 re-measurement): wrong
# picks all negative (max -0.027), right picks all positive (min +0.016) —
# the floor sits inside the gap. Move it only with a new measurement, never
# by tuning against a failing run.
MARGIN_FLOOR = 0.01
# neighbours only: just above the measured background-argmax max (0.054);
# the target is margin-gated instead because right-pick scores overlap the
# background range (ibuprofen right picks reach 0.007)
NEIGHBOUR_SCORE_FLOOR = 0.055
# ~0.7 s/frame detection vs 66.7 ms between pairs: throttle refusal retries
MIN_RETRY_GAP_NS = int(1e9)


def _contains_centre(box: list, of: list) -> bool:
    """`box` contains the centre of `of` — the runtime analogue of the
    offline rule (rival boxes containing the ground-truth centroid). Any-
    overlap was the first cut and is the WRONG population: a neighbour's
    correct box grazing the target's by a pixel is scene layout, not
    identity ambiguity (round-2 review)."""
    cx, cy = (of[0] + of[2]) / 2, (of[1] + of[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def pick_target_detection(detections: list[dict], target: str, margin_floor: float) -> dict:
    """The target's best detection WITH its identity margin, or PoseRefused.

    The margin is score over the best different-label rival whose box
    CONTAINS THE CENTRE of the target's box. Below the floor is the
    measured wrong-pick signature (all negative under this rule): refuse."""
    mine = [d for d in detections if d["label"] == target]
    if not mine:
        raise PoseRefused(f"no {target!r} detection in frame (L2)")
    best = max(mine, key=lambda d: d["score"])
    if not all(np.isfinite(v) for v in best["box"]):
        raise PoseRefused(f"non-finite detection box for {target!r}")
    rivals = [
        d["score"]
        for d in detections
        if d["label"] != target and _contains_centre(d["box"], best["box"])
    ]
    margin = best["score"] - max(rivals, default=0.0)
    if margin < margin_floor:
        raise PoseRefused(
            f"identity margin {margin:.3f} under the {margin_floor} floor — measured "
            "wrong picks all carry NEGATIVE margins under this rule; a low-margin pick "
            "risks the 10x wrong_object penalty, a refusal costs 1x"
        )
    return {**best, "margin": margin}


def _bbox_mask(shape: tuple, box: list) -> np.ndarray:
    """Synthetic mask for a detection box; a malformed box (non-finite or
    inverted) raises PoseRefused rather than ValueError — one bad frame
    must refuse, not kill the pose source (round-2 review). A degenerate
    or out-of-frame box collapses to an empty mask and hits
    estimate_pose's min-pixel refusal."""
    if not all(np.isfinite(v) for v in box) or box[2] < box[0] or box[3] < box[1]:
        raise PoseRefused(f"malformed detection box {box}")
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    mask = np.zeros(shape, dtype=np.int32)
    mask[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = 1
    return mask


@dataclass(kw_only=True)
class L2Session(FramePairSession):
    """Detection-driven estimate over the shared frame-pairing lifecycle.

    `detector(rgb) -> [{label, score, box}]` and `backprojector` are
    injected so every rule above is unit-testable without models or dora."""

    detector: Callable[[np.ndarray], list[dict]] | None = None
    backprojector: Callable[[dict], Callable] | None = None
    margin_floor: float = MARGIN_FLOOR
    retry_gap_ns: int = MIN_RETRY_GAP_NS
    last_attempt_ns: int = -(10**18)

    # the L2 observation frame is the rendered rgb
    on_rgb = FramePairSession.on_obs

    def on_reset_done(self) -> None:
        super().on_reset_done()
        # sim time is monotonic across teleports: a stale clock would
        # silently throttle the next episode's first attempts
        self.last_attempt_ns = -(10**18)

    def _estimate(self, rgb: np.ndarray, depth: np.ndarray) -> dict | None:
        stamp = self.latest_depth[0]
        if stamp - self.last_attempt_ns < self.retry_gap_ns:
            return None  # throttled: not an attempt, request stays pending
        self.last_attempt_ns = stamp
        detections = self.detector(rgb)
        best = pick_target_detection(detections, self.target, self.margin_floor)
        project = self.backprojector(self.calibration)
        size = self.meds[self.target]["size"]
        estimate = estimate_pose(
            _bbox_mask(depth.shape, best["box"]),
            depth,
            [1],
            float(size[2]),
            project,
            footprint_m=tuple(size[:2]),
        )
        # positional neighbour slots (the grasp planner zips MED_NAMES
        # strict=True): each med's best CONFIDENT detection, None + counted
        # below the score floor. The floor is what makes None reachable in
        # production: at threshold 0 every label always has candidate boxes,
        # and an unfloored slot would be filled by a background argmax box —
        # a guessed obstacle position with the audit trail reading confident
        # (round-2 review). No margin gate: a mislabeled but CONFIDENT box
        # still marks a real obstacle, and over-constraining is the safe
        # direction.
        neighbours, refused = [], 0
        for name, spec in self.meds.items():
            cands = [
                d for d in detections if d["label"] == name and d["score"] >= NEIGHBOUR_SCORE_FLOOR
            ]
            if not cands:
                neighbours.append(None)
                refused += 1
                continue
            box = max(cands, key=lambda d: d["score"])["box"]
            try:
                n_est = estimate_pose(
                    _bbox_mask(depth.shape, box), depth, [1], float(spec["size"][2]), project
                )
            except PoseRefused:
                neighbours.append(None)
                refused += 1
                continue
            neighbours.append([n_est["pos"][0], n_est["pos"][1]])
        return {
            **estimate,
            "target_med": self.target,
            "neighbours": neighbours,
            "neighbours_refused": refused,
            "detection": {
                "score": float(best["score"]),
                "margin": float(best["margin"]),
                "box": [float(v) for v in best["box"]],
            },
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
    session = L2Session(
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
                # TC-9: refuse rather than publish a doubtful pose; the
                # episode closes honestly on the verifier's timeout
                print(f"L2 pose refused for {session.target}: {exc}", file=sys.stderr)
                continue
            if out is None:
                continue
            # drop-in for the same target_pose edge as L0/L1; frame image
            # keys stay behind, detection evidence rides for the audit
            send(
                "target_pose",
                pa.array(np.asarray(out["pos"] + [0.0, 0.0, 0.0, 1.0], dtype=np.float32)),
                {
                    **{k: v for k, v in metadata.items() if k not in ("enc", "h", "w")},
                    "target_med": out["target_med"],
                    "neighbours": json.dumps(out["neighbours"]),
                    "neighbours_refused": out["neighbours_refused"],
                    "mask_pixels": out["mask_pixels"],
                    "detection": json.dumps(out["detection"]),
                    "perception": "L2",
                },
            )


if __name__ == "__main__":  # pragma: no cover
    main()
