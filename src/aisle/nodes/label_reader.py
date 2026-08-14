"""ocr-label node (CAP-5): read a med box's front label with the wrist camera.

T2 (design doc §3): the medicine is identified by the printed label word
only — no color prior. The recipe was measured under idea I13 (closed
`up` on campaign/phase2-t1-baselines): with the arm parked at a read
pose in front of the box face, take per-hypothesis crops around the
face centre (med SIZES are public config; identity is what's being
read), ROTATE_270 to undo the label texture's wrist-roll orientation,
and score ink-probability NCC against the deterministic label
templates. Margin floor pre-registered at 0.04; measured min margin was
+0.227 at the exact read pose and +0.226 under 5 mm read-pose noise.

Service pattern (TC-6 style): a `read_request` arms the session; the
first wrist frame whose SIM timestamp is strictly after the completed
read park is read once, and `read_result` replies with the request's
`request_id`. This freshness barrier makes frame selection independent
of wall-clock delivery order (CON-5/TC-2).

The wrist input stays LATEST-WINS (expert_t2.yaml queue_size 1) and
should not be "fixed" to a deep queue: measured on a live T2 episode,
the park barrier is cleared by the very next delivered frame (20-30 ms
of sim time later, eight for eight reads), because depth-1 delivery
always hands this slow consumer the newest rendered frame. A deep queue
instead makes it drain a backlog — at queue_size 120 the same episode
read frames stamped ~60 SIM SECONDS before their own park (PR #176
review measured both).

Reading below the margin floor refuses (label null) rather than
guessing — a wrong-med grasp is the one outcome T2 exists to prevent
(TC-9 refusal discipline) — and so does a barrier no frame clears before
its simulation-time deadline, so every armed request replies exactly once
even when the wrist stream emits no frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from aisle.topics import parse_sim_stamp

MARGIN_FLOOR = 0.04  # pre-registered with I13, before any live read ran
# pitched (look-down) views can slide enough neighbour texture into the
# resample to produce a confident WRONG read (measured: +0.093 on a
# neighbour label); correct pitched reads measure +0.28 — the raised
# floor separates them with margin on both sides
PITCHED_MARGIN_FLOOR = 0.15
# below this many pixels a hypothesis crop carries no legible signal —
# refuse instead of scoring noise (the 16 px synthetic legibility floor)
MIN_CROP_PX = 12
# the label texture is authored upright on the UV face as seen by the
# ROLLED wrist view; a frame-axis-aligned crop must be unrolled by 270°
# (measured: the other three rotations score 1/5 on the same crops)
LABEL_ROTATION_K = 3  # np.rot90 quarter-turns
# The park-complete event crosses ik -> state machine -> reader while wrist
# RGB continues at 30 Hz. Retain enough already-consumed frames to recover
# the first post-park sample even when that dialogue is delayed by ~1 sim-s.
FRAME_BUFFER_SIZE = 32
# Independent sim-time deadline for an armed read. A bridge joint-state
# heartbeat drives it even if the wrist camera emits zero events; unlike a
# delivered-frame count, latest-wins queue drops cannot move the deadline.
READ_TIMEOUT_SIM_NS = 3_000_000_000


def ink_prob(image: np.ndarray, blur_px: float = 1.0) -> np.ndarray:
    """Colorless ink map in [0, 1]: grayscale, Gaussian blur, min-max
    normalize, flip so ink (the minority class) is high. Color-blind by
    construction — T2's no-color-prior rule holds at the feature level."""
    from PIL import Image, ImageFilter

    gray = np.asarray(
        Image.fromarray(image).convert("L").filter(ImageFilter.GaussianBlur(blur_px)),
        dtype=np.float64,
    )
    lo, hi = float(gray.min()), float(gray.max())
    if hi - lo < 10.0:  # flat crop: no text signal at 8-bit depth
        return np.zeros_like(gray)
    p = (gray - lo) / (hi - lo)
    return 1.0 - p if float((p > 0.5).mean()) > 0.5 else p


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def label_templates(meds: dict) -> dict[str, np.ndarray]:
    """Deterministic colorless template images, one per med (CON-5: the
    same frozen texture function the scene prints the labels with)."""
    from aisle.scenes.pharmacy import label_texture_image

    return {
        name: label_texture_image(spec["label"], [1.0, 1.0, 1.0, 1.0])
        for name, spec in meds.items()
    }


@dataclass(frozen=True, kw_only=True)
class ReadVerdict:
    label: str | None  # None = refused (margin below floor or no signal)
    margin: float
    scores: dict[str, float]


def verdict_from_scores(scores: dict[str, float], floor: float = MARGIN_FLOOR) -> ReadVerdict:
    """The margin rule (pre-registered with I13): the best hypothesis
    wins only when it beats the runner-up by the floor AND correlates
    positively at all — otherwise refuse (TC-9: a wrong-med grasp is
    10x worse than a timeout)."""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    margin = ranked[0][1] - ranked[1][1]
    label = ranked[0][0] if margin >= floor and ranked[0][1] > 0.0 else None
    return ReadVerdict(label=label, margin=margin, scores=scores)


# rectification corner order: output-template NW, SW, SE, NE as
# (y_sign, z_sign) offsets on the face plane. Derived from the crop
# recipe's rot90-CW unroll (view left = world +y, view up = world +z;
# CW rotation maps crop bottom-left -> template top-left) and pinned by
# measurement: this order reads 5/5 at min margin +0.301, the other
# orientations score at noise level.
RECTIFY_ORDER = ((+1, -1), (-1, -1), (-1, +1), (+1, +1))
# residual L2 estimate error along the shelf depth (x) and lateral (y)
# axes, recovered by the GLOBAL alignment search in read_face_rectified.
# 5 mm steps: the NCC peak is ~5 mm wide (measured 0.852 at the exact
# correction, 0.207 five mm away), so a 1 cm grid steps OVER it
ALIGN_SEARCH_DX = tuple(round(v * 0.005, 3) for v in range(-4, 5))
ALIGN_SEARCH_DY = tuple(round(v * 0.005, 3) for v in range(-2, 3))


def shelf_board_tops() -> tuple[float, ...]:
    """Board-top z per shelf level, from the frozen scene config (SCN-2:
    geometry lives in physics.toml — this is public layout, not oracle
    state). Every box RESTS on a board, so a hypothesis's centre z is
    fully determined: board_top + sz/2. The toured face z is an L2
    ESTIMATE measured ~1-2.5 cm low (live run 7: 0.090 vs true
    0.102-0.115 — the quad landed half a label-height low and every read
    refused); snapping z to the geometry removes that error EXACTLY,
    with no score search (a best-of-offsets search compressed margins
    4x — every wrong hypothesis maxes its own noise)."""
    import os

    from aisle.scenes.pharmacy import load_physics, resolve_layout

    physics = load_physics()
    layout = resolve_layout(physics, os.environ.get("AISLE_EMBODIMENT", "franka"))
    shelf = layout["shelf"]
    return tuple(
        float(shelf["pos"][2] + height + shelf["board_thickness"] / 2.0)
        for height in shelf["level_heights"]
    )


def read_face_rectified(
    rgb: np.ndarray,
    face: np.ndarray,
    cam_pos: np.ndarray,
    cam_rot_cv: np.ndarray,
    meds: dict,
    templates: dict[str, np.ndarray],
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    floor: float = MARGIN_FLOOR,
    board_tops: tuple[float, ...] | None = None,
) -> ReadVerdict:
    """Perspective-rectified read: project each hypothesis's face quad
    through the ACHIEVED camera pose (executor FK) and resample straight
    into template space. Exact under tilt — the axis-aligned crop path
    degrades to noise on oblique ladder poses (a far under-board read is
    a 32 px tilted face), where this reads 5/5 at min margin +0.301.

    With `board_tops`, each hypothesis's centre z is snapped to its OWN
    resting height on the nearest shelf level (see shelf_board_tops) —
    the estimated z is used only to pick the level. The residual x/y
    estimate error (~1 cm) is recovered by a small alignment search
    with ONE offset chosen globally: argmax over (offset, hypothesis)
    jointly, then EVERY hypothesis scored at that single offset. A
    per-hypothesis best-of-offsets maxes each wrong hypothesis's noise
    and compressed margins 4x (measured); the global offset keeps the
    margin comparison a single aligned view."""
    from PIL import Image

    face = np.asarray(face, dtype=np.float64)
    cam_pos = np.asarray(cam_pos, dtype=np.float64)
    cam_rot_cv = np.asarray(cam_rot_cv, dtype=np.float64).reshape(3, 3)
    image = Image.fromarray(rgb)

    def score_at(offset: np.ndarray) -> dict[str, float]:
        scores: dict[str, float] = {}
        for name, spec in meds.items():
            _, sy, sz = (float(v) for v in spec["size"])
            centre = face + offset
            if board_tops:
                level = min(board_tops, key=lambda top: abs(top + sz / 2.0 - face[2]))
                centre[2] = level + sz / 2.0
            quad: list[float] = []
            behind = False
            for y_sign, z_sign in RECTIFY_ORDER:
                corner = centre + np.array([0.0, y_sign * sy / 2.0, z_sign * sz / 2.0])
                rel = cam_rot_cv.T @ (corner - cam_pos)
                if rel[2] <= 1e-6:
                    behind = True
                    break
                quad += [rel[0] / rel[2] * fx + cx, rel[1] / rel[2] * fy + cy]
            if behind:
                scores[name] = -1.0
                continue
            template = templates[name]
            rect = np.asarray(
                image.transform(
                    (template.shape[1], template.shape[0]), Image.QUAD, quad, Image.BILINEAR
                )
            )
            scores[name] = ncc(ink_prob(rect), ink_prob(template))
        return scores

    best_scores: dict[str, float] | None = None
    for dx in ALIGN_SEARCH_DX:
        for dy in ALIGN_SEARCH_DY:
            scores = score_at(np.array([dx, dy, 0.0]))
            if best_scores is None or max(scores.values()) > max(best_scores.values()):
                best_scores = scores
    assert best_scores is not None
    return verdict_from_scores(best_scores, floor=floor)


def read_face(
    rgb: np.ndarray,
    centre_px: tuple[float, float],
    meds: dict,
    templates: dict[str, np.ndarray],
    fx: float,
    fy: float,
    range_m: float,
) -> ReadVerdict:
    """Score every med hypothesis at its own projected crop geometry and
    apply the margin rule. Each hypothesis crop is sized by THAT med's
    face (width sy -> u via fx, height sz -> v via fy at `range_m`), so
    a template is always compared at its native aspect."""
    from PIL import Image

    h, w = rgb.shape[:2]
    cx, cy = float(centre_px[0]), float(centre_px[1])
    scores: dict[str, float] = {}
    for name, spec in meds.items():
        _, sy, sz = (float(v) for v in spec["size"])
        half_w = int(fx * (sy / 2.0) / range_m)
        half_h = int(fy * (sz / 2.0) / range_m)
        x0, x1 = max(0, int(cx) - half_w), min(w, int(cx) + half_w)
        y0, y1 = max(0, int(cy) - half_h), min(h, int(cy) + half_h)
        if x1 - x0 < MIN_CROP_PX or y1 - y0 < MIN_CROP_PX:
            scores[name] = -1.0
            continue
        crop = np.rot90(rgb[y0:y1, x0:x1], k=LABEL_ROTATION_K)
        crop_ink = ink_prob(crop)
        template = templates[name]
        resized = np.asarray(
            Image.fromarray(template).resize((crop_ink.shape[1], crop_ink.shape[0]), Image.LANCZOS)
        )
        scores[name] = ncc(crop_ink, ink_prob(resized))
    return verdict_from_scores(scores)


DETECT_SCORE_FLOOR = 0.1  # below this OWLv2 emits background clutter
# a read pose parks the face near the AIM PRIOR (the executor projects
# the toured face point through the achieved arm pose); boxes further
# out are neighbours leaking into view, not the toured box. Tight on
# purpose: with a 60 px frame-centre radius the first live tour centred
# on a neighbour and read it confidently wrong.
DETECT_MAX_OFFSET_PX = 45.0


def detect_box_centre(
    rgb: np.ndarray,
    med_names: list[str],
    model_pair,
    prior_px: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """OWLv2 centre of the parked box: best-scoring detection near the
    aim prior (frame centre when the executor sent none), DETECTION
    LABELS DISCARDED. The color-worded med vocabulary is used purely as
    saliency anchors to localize a box (neutral "a box" queries score
    0.03-0.10 on these frames — below any usable floor — while the
    color-worded queries anchor at 0.2+); identity comes from reading
    the label word, so T2's no-color-prior rule holds where it matters.
    None when nothing qualifies. Measured: fixed frame-centre crops
    refuse 3/5 under 5 mm read-pose noise; detector-centred crops read
    5/5 with min margin +0.226."""
    from aisle.verifier.models import detect_meds

    h, w = rgb.shape[:2]
    px, py = prior_px if prior_px is not None else (w / 2.0, h / 2.0)
    best: tuple[float, float, float] | None = None  # (score, cx, cy)
    for det in detect_meds(rgb, med_names, model_pair=model_pair):
        if det["score"] < DETECT_SCORE_FLOOR:
            continue
        x0, y0, x1, y1 = det["box"]
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if (cx - px) ** 2 + (cy - py) ** 2 > DETECT_MAX_OFFSET_PX**2:
            continue
        if best is None or det["score"] > best[0]:
            best = (det["score"], cx, cy)
    return None if best is None else (best[1], best[2])


@dataclass(kw_only=True)
class ReaderSession:
    """Once-per-request read of the first frame strictly newer than the
    completed park's sim-time barrier. Queued RGB and request events can
    arrive in either wall order; only sim order decides eligibility.

    Every armed request REPLIES exactly once: an eligible frame answers it,
    or the independent sim-stamped heartbeat expires it even if the camera
    emits no events. The deadline is a function of simulation time, not host
    scheduling or queue drops (CON-5)."""

    meds: dict
    templates: dict[str, np.ndarray]
    # per-level board-top z for hypothesis z snapping (shelf_board_tops)
    board_tops: tuple[float, ...] | None = None
    # (rgb, prior_px | None) -> (cx, cy) | None; None = use the prior
    find_centre: object = None
    intrinsics: dict | None = None
    pending: dict | None = field(default=None)
    recent_frames: list[tuple[int, np.ndarray]] = field(default_factory=list, repr=False)
    last_read_sim_time_ns: int = field(default=-1, init=False)
    # sim stamp of the newest reset consumed; a request barriered at or
    # before it belongs to the previous episode (see on_reset_done)
    last_reset_sim_time_ns: int = field(default=-1, init=False)

    def on_bridge_info(self, info: dict) -> None:
        self.intrinsics = info["calibration"]["wrist"]["intrinsics"]

    def on_read_request(self, request: dict, request_id: str) -> dict | None:
        barrier = parse_sim_stamp({"sim_time_ns": request.get("frame_after_sim_time_ns", 0)})
        if barrier is not None and barrier <= self.last_reset_sim_time_ns:
            # reset_done and read_request are independent queues. Reply to a
            # delayed request from the ended episode without replacing a live
            # request already armed for the new episode.
            return self._refusal(request_id, "stale_read_request")
        pose = None
        if request.get("cam_pos") is not None and request.get("cam_rot_cv") is not None:
            pose = {
                "face": np.asarray(request["face"], dtype=np.float64),
                "cam_pos": np.asarray(request["cam_pos"], dtype=np.float64),
                "cam_rot_cv": np.asarray(request["cam_rot_cv"], dtype=np.float64).reshape(3, 3),
            }
        centre = request.get("centre_px")
        # An ABSENT key is the unbarriered path (manual requests, and a park
        # whose own stamp was unusable): the next delivered frame answers it,
        # which is this reader's pre-barrier behavior. A present key is
        # always a real sim time — the executor never sends a zero barrier,
        # because a zero would be cleared by every stamped frame including
        # mid-park ones (PR #176 review).
        self.pending = {
            "request_id": request_id,
            "range_m": float(request["range_m"]),
            "frame_after_sim_time_ns": barrier,
            "centre_px": (float(centre[0]), float(centre[1])) if centre else None,
            "pose": pose,
            "floor": PITCHED_MARGIN_FLOOR if request.get("pitched") else MARGIN_FLOOR,
        }
        if barrier is None or self.intrinsics is None:
            return None
        eligible = [(stamp, frame) for stamp, frame in self.recent_frames if stamp > barrier]
        if not eligible:
            return None
        stamp, frame = min(eligible, key=lambda item: item[0])
        self.recent_frames = [item for item in self.recent_frames if item[0] > stamp]
        return self._read(frame, stamp)

    def on_reset_done(self, sim_time_ns: int | None = None) -> None:
        """Drop everything belonging to the ended episode.

        Fenced by sim time (PR #176 review): reset_done and read_request
        reach this node on independent queues, so a reset delayed past the
        next episode's first request would otherwise clear a LIVE request
        and hang that tour — the state machine ignores replies it is not
        awaiting, so nothing downstream would ever notice. A request
        barriered strictly after the reset is from the new episode and
        survives; frames are kept on the same rule.

        A stamp-less call takes the UNFENCED branch below — the destructive
        one — so the caller must only make it for a real boundary. Since
        issue #192 the boundary arrives from the reset service, which also
        replies to REFUSED resets (ADR-8) carrying no sim stamp; those are
        filtered at the call site, because a refusal is not a boundary and
        clearing a live request on one is exactly the hang this fence was
        built to prevent."""
        reset_ns = sim_time_ns if sim_time_ns is not None else None
        if reset_ns is not None and reset_ns > self.last_reset_sim_time_ns:
            self.last_reset_sim_time_ns = reset_ns
        if reset_ns is None:
            self.pending = None
            self.recent_frames.clear()
        else:
            barrier = (self.pending or {}).get("frame_after_sim_time_ns")
            if barrier is None or barrier <= reset_ns:
                self.pending = None
            self.recent_frames = [item for item in self.recent_frames if item[0] > reset_ns]
        self.last_read_sim_time_ns = -1

    def on_rgb(self, rgb: np.ndarray, sim_time_ns: int | None = None) -> dict | None:
        """Return one read_result for the first sim-eligible frame."""
        stamp = sim_time_ns if sim_time_ns is not None and sim_time_ns > 0 else None
        if stamp is not None:
            self.recent_frames.append((stamp, rgb))
            self.recent_frames.sort(key=lambda item: item[0])
            self.recent_frames = self.recent_frames[-FRAME_BUFFER_SIZE:]
        if self.pending is None or self.intrinsics is None:
            return None
        barrier = self.pending["frame_after_sim_time_ns"]
        if barrier is not None and (stamp is None or stamp <= barrier):
            return None  # the independent sim heartbeat owns the deadline
        if stamp is not None:
            self.recent_frames = [item for item in self.recent_frames if item[0] > stamp]
        return self._read(rgb, stamp)

    def on_clock(self, sim_time_ns: int | None) -> dict | None:
        """Expire a barriered request from an independent SIM heartbeat."""
        if self.pending is None or sim_time_ns is None:
            return None
        barrier = self.pending["frame_after_sim_time_ns"]
        if barrier is None or sim_time_ns <= barrier + READ_TIMEOUT_SIM_NS:
            return None
        return self._refuse_pending("no_frame_after_park")

    @staticmethod
    def _refusal(request_id: str, reason: str) -> dict:
        return {
            "request_id": request_id,
            "label": None,
            "margin": 0.0,
            "scores": {},
            "reason": reason,
        }

    def _refuse_pending(self, reason: str) -> dict:
        """Reply with a refusal and release the current request."""
        pending, self.pending = self.pending, None
        self.last_read_sim_time_ns = -1
        return self._refusal(pending["request_id"], reason)

    def _read(self, rgb: np.ndarray, sim_time_ns: int | None) -> dict:
        """Consume the pending request against one sim-eligible frame."""
        assert self.pending is not None
        self.last_read_sim_time_ns = sim_time_ns if sim_time_ns is not None else -1
        pending, self.pending = self.pending, None
        h, w = rgb.shape[:2]
        if pending["pose"] is not None:
            # the executor sent the achieved camera pose: rectify — exact
            # under tilt, no detector in the loop
            pose = pending["pose"]
            verdict = read_face_rectified(
                rgb,
                pose["face"],
                pose["cam_pos"],
                pose["cam_rot_cv"],
                self.meds,
                self.templates,
                fx=float(self.intrinsics["fx"]),
                fy=float(self.intrinsics["fy"]),
                cx=w / 2.0,
                cy=h / 2.0,
                floor=pending["floor"],
                board_tops=self.board_tops,
            )
            return {
                "request_id": pending["request_id"],
                "label": verdict.label,
                "margin": verdict.margin,
                "scores": verdict.scores,
            }
        prior = pending["centre_px"]
        centre_px = None
        if self.find_centre is not None:
            centre_px = self.find_centre(rgb, prior)
        if centre_px is None:
            # no qualifying detection: read AT the aim prior — the
            # geometric projection of the toured face beats a blind
            # frame centre
            centre_px = prior if prior is not None else (w / 2.0, h / 2.0)
        verdict = read_face(
            rgb,
            centre_px,
            self.meds,
            self.templates,
            fx=float(self.intrinsics["fx"]),
            fy=float(self.intrinsics["fy"]),
            range_m=pending["range_m"],
        )
        return {
            "request_id": pending["request_id"],
            "label": verdict.label,
            "margin": verdict.margin,
            "scores": verdict.scores,
        }


def main() -> None:  # pragma: no cover — dora runtime
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import load_meds
    from aisle.topics import make_sender
    from aisle.verifier.models import load_pinned

    node = Node()
    send = make_sender(node)
    meds = load_meds()
    model_pair = load_pinned("identity")
    med_names = list(meds)
    session = ReaderSession(
        meds=meds,
        templates=label_templates(meds),
        board_tops=shelf_board_tops(),
        find_centre=lambda rgb, prior: detect_box_centre(rgb, med_names, model_pair, prior),
    )

    def publish_result(result: dict | None, barrier: int | None = None) -> None:
        if result is None:
            return
        # Protocol refusals have no supporting image. Do not accidentally
        # stamp one with the previous successful read's session-level value.
        read_stamp = -1 if result.get("reason") else session.last_read_sim_time_ns
        if result.get("reason") == "no_frame_after_park":
            print(
                f"read refused: no wrist frame before the park deadline ({result['request_id']})",
                file=sys.stderr,
            )
        elif result.get("reason") == "stale_read_request":
            print(f"read refused: stale request {result['request_id']}", file=sys.stderr)
        elif result["label"] is None:
            print(
                f"read refused: margin {result['margin']:.3f} < {MARGIN_FLOOR}",
                file=sys.stderr,
            )
        # The barrier's own evidence line (PR #176 review): the live graph
        # test asserts every anchored read chose a frame STRICTLY after its
        # park, which is what proves the mechanism worked in a real run —
        # an outcome assertion cannot, because CON-5 layer (d) makes the
        # episode outcome statistical.
        if barrier is not None and read_stamp >= 0:
            print(
                f"read anchored: barrier={barrier} frame={read_stamp} ({result['request_id']})",
                file=sys.stderr,
            )
        output_metadata = {"request_id": result["request_id"]}
        if read_stamp >= 0:
            output_metadata["sim_time_ns"] = read_stamp
        send("read_result", pa.array([json.dumps(result)]), output_metadata)

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "bridge_info":
            session.on_bridge_info(json.loads(event["value"][0].as_py()))
        elif event["id"] == "read_request":
            request = json.loads(event["value"][0].as_py())
            # read the barrier BEFORE the call: an immediately-answered
            # request has already cleared session.pending by the time it
            # returns, and the anchored-read line needs the barrier it used
            barrier = parse_sim_stamp({"sim_time_ns": request.get("frame_after_sim_time_ns", 0)})
            publish_result(
                session.on_read_request(request, metadata.get("request_id", "")), barrier
            )
        elif event["id"] == "reset_done":
            # every reply here IS a boundary (ADR-34): refusals ride
            # `reset_refused`, which this node does not subscribe to. The
            # `metadata["error"]` filter that used to stand here — a
            # stamp-less refusal takes on_reset_done's unfenced branch and
            # clears a LIVE read request, hanging the tour silently — is
            # deleted because the shape, not this node, now prevents it.
            session.on_reset_done(parse_sim_stamp(metadata))
        elif event["id"] == "clock":
            barrier = (session.pending or {}).get("frame_after_sim_time_ns")
            publish_result(session.on_clock(parse_sim_stamp(metadata)), barrier)
        elif event["id"] == "rgb_wrist":
            h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
            if h <= 0 or w <= 0:
                print(f"rgb_wrist frame skipped: h={h} w={w}", file=sys.stderr)
                continue
            frame = np.asarray(event["value"].to_numpy(zero_copy_only=False))
            barrier = (session.pending or {}).get("frame_after_sim_time_ns")
            publish_result(
                session.on_rgb(
                    frame.astype(np.uint8).reshape(h, w, 3),
                    sim_time_ns=parse_sim_stamp(metadata),
                ),
                barrier,
            )


if __name__ == "__main__":  # pragma: no cover
    main()
