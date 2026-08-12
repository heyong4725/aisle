"""ocr-label reader core (CAP-5; design doc §3 T2; idea I13).

The synthetic frames below emulate what the wrist camera delivers at a
read pose: the label texture appears ROTATED by the inverse of the
reader's unroll constant, at the med face's projected size. Building
them with np.rot90(k=1) pins LABEL_ROTATION_K=3 (np.rot90 k=1 and k=3
are exact inverses): mutate the constant and the read refuses.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from aisle.nodes.label_reader import (
    MARGIN_FLOOR,
    ReaderSession,
    detect_box_centre,
    ink_prob,
    label_templates,
    read_face,
    verdict_from_scores,
)
from aisle.scenes.pharmacy import load_meds

FX = FY = 171.4  # 240 px at 70 deg vertical fov
RANGE_M = 0.13

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def meds():
    return load_meds()


@pytest.fixture(scope="module")
def templates(meds):
    return label_templates(meds)


def camera_view_frame(
    meds, templates, name: str, rotation_k: int = 1, centre=(160, 120)
) -> np.ndarray:
    """A wrist-like frame: uniform background and the med's label pasted
    so the reader's pipeline (crop 2hw x 2hh at `centre`, unroll k=3,
    resize-compare) reconstructs the upright template EXACTLY. The
    camera-view paste is the inverse: the template resized to the
    crop's post-unroll shape, re-rolled by k=1 (k=1 and k=3 are exact
    inverses). rotation_k=0 breaks that inverse for the pin test."""
    frame = np.full((240, 320, 3), 96, dtype=np.uint8)
    _, sy, sz = (float(v) for v in meds[name]["size"])
    half_w = int(FX * (sy / 2) / RANGE_M)
    half_h = int(FY * (sz / 2) / RANGE_M)
    face = np.asarray(
        Image.fromarray(templates[name]).resize((2 * half_h, 2 * half_w), Image.LANCZOS)
    )[..., :3]
    view = np.rot90(face, k=rotation_k)
    vh, vw = view.shape[:2]
    cx, cy = centre
    frame[cy - vh // 2 : cy - vh // 2 + vh, cx - vw // 2 : cx - vw // 2 + vw] = view
    return frame


class TestTemplates:
    def test_templates_are_deterministic(self, meds):
        """CON-5: same frozen texture function, same bytes."""
        a, b = label_templates(meds), label_templates(meds)
        assert all(np.array_equal(a[n], b[n]) for n in a)

    def test_one_template_per_med(self, meds, templates):
        assert set(templates) == set(meds)


class TestReadFace:
    def test_reads_every_med_from_its_camera_view(self, meds, templates):
        """The recipe end-to-end on synthetic frames: correct label with
        margin above the pre-registered floor, for every med."""
        for name in meds:
            frame = camera_view_frame(meds, templates, name)
            verdict = read_face(frame, (160, 120), meds, templates, FX, FY, RANGE_M)
            assert verdict.label == name
            assert verdict.margin >= MARGIN_FLOOR

    def test_unroll_direction_is_pinned_by_score_asymmetry(self, meds, templates):
        """The k=3 pin: the correctly-rolled frame (k=1 paste) must
        outscore the reverse-rolled one (k=3 paste, which unrolls to
        upside-down text) by a wide gap for the SAME med — mutating
        LABEL_ROTATION_K to 1 swaps which frame wins and fails this."""
        correct = read_face(
            camera_view_frame(meds, templates, "metformin", rotation_k=1),
            (160, 120),
            meds,
            templates,
            FX,
            FY,
            RANGE_M,
        )
        reverse = read_face(
            camera_view_frame(meds, templates, "metformin", rotation_k=3),
            (160, 120),
            meds,
            templates,
            FX,
            FY,
            RANGE_M,
        )
        assert correct.scores["metformin"] > reverse.scores["metformin"] + 0.1

    def test_blank_frame_is_refused(self, meds, templates):
        frame = np.full((240, 320, 3), 96, dtype=np.uint8)
        verdict = read_face(frame, (160, 120), meds, templates, FX, FY, RANGE_M)
        assert verdict.label is None

    def test_tiny_projected_crops_are_refused_not_scored(self, meds, templates):
        """MIN_CROP_PX: at 10 m every hypothesis crop is sub-legible;
        scoring noise there once produced confident nonsense."""
        frame = camera_view_frame(meds, templates, "metformin")
        verdict = read_face(frame, (160, 120), meds, templates, FX, FY, range_m=10.0)
        assert verdict.label is None
        assert all(s == -1.0 for s in verdict.scores.values())

    def test_off_centre_label_read_via_detected_centre(self, meds, templates):
        """The centre argument matters: the same frame read at the true
        paste centre succeeds after failing at a 40 px-off guess."""
        name = "cetirizine"
        frame = camera_view_frame(meds, templates, name, centre=(200, 120))
        assert read_face(frame, (200, 120), meds, templates, FX, FY, RANGE_M).label == name
        assert read_face(frame, (160, 120), meds, templates, FX, FY, RANGE_M).label != name


class TestVerdictRule:
    def test_wide_margin_wins(self):
        verdict = verdict_from_scores({"a": 0.5, "b": 0.3, "c": 0.1})
        assert verdict.label == "a"
        assert verdict.margin == pytest.approx(0.2)

    def test_sub_floor_margin_refuses(self):
        """MARGIN_FLOOR pin: a near-tie must refuse, however high the
        absolute scores are."""
        assert verdict_from_scores({"a": 0.9, "b": 0.9 - MARGIN_FLOOR / 2}).label is None

    def test_just_above_floor_reads(self):
        assert verdict_from_scores({"a": 0.5, "b": 0.5 - MARGIN_FLOOR * 1.01}).label == "a"

    def test_negative_best_score_refuses_even_with_margin(self):
        """All-negative correlations mean the crop matches NOTHING —
        margin alone must not manufacture confidence."""
        assert verdict_from_scores({"a": -0.02, "b": -0.5}).label is None


class TestInkProb:
    def test_flat_image_has_no_ink(self):
        assert ink_prob(np.full((32, 32, 3), 120, dtype=np.uint8)).max() == 0.0

    def test_ink_is_the_minority_class_regardless_of_polarity(self):
        light = np.full((32, 32, 3), 220, dtype=np.uint8)
        light[10:14, 4:28] = 20  # dark ink on light face
        dark = np.full((32, 32, 3), 30, dtype=np.uint8)
        dark[10:14, 4:28] = 230  # light ink on dark face
        assert ink_prob(light)[12, 16] > 0.8
        assert ink_prob(dark)[12, 16] > 0.8


class TestReaderSession:
    def _session(self, meds, templates, find_centre=None):
        session = ReaderSession(meds=meds, templates=templates, find_centre=find_centre)
        session.on_bridge_info({"calibration": {"wrist": {"intrinsics": {"fx": FX, "fy": FY}}}})
        return session

    def test_reads_once_per_request(self, meds, templates):
        """TC-6 service discipline: one reply per request_id; further
        frames are ignored until the next request."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "omeprazole")
        assert session.on_rgb(frame) is None  # no request yet
        session.on_read_request({"range_m": RANGE_M}, "ep-1/read0")
        result = session.on_rgb(frame)
        assert result["request_id"] == "ep-1/read0"
        assert result["label"] == "omeprazole"
        assert session.on_rgb(frame) is None  # one-shot

    def test_read_waits_for_frame_strictly_after_park_sim_time(self, meds, templates):
        """CON-5/TC-2: queued frames at or before the completed read park
        must not consume the request. The first eligible frame is selected
        by sim time, independent of wall-clock request/frame arrival order."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "omeprazole")
        session.on_read_request(
            {"range_m": RANGE_M, "frame_after_sim_time_ns": 42},
            "ep-1/read0",
        )

        assert session.on_rgb(frame, sim_time_ns=41) is None
        assert session.on_rgb(frame, sim_time_ns=42) is None
        result = session.on_rgb(frame, sim_time_ns=43)
        assert result["request_id"] == "ep-1/read0"
        assert result["label"] == "omeprazole"

    def test_missing_frame_stamp_cannot_bypass_park_barrier(self, meds, templates):
        """CON-5/TC-2: a stamped request fails closed on an unstamped RGB
        frame, while retaining the request for a later eligible frame."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "metformin")
        session.on_read_request(
            {"range_m": RANGE_M, "frame_after_sim_time_ns": 10},
            "ep-1/read0",
        )

        assert session.on_rgb(frame) is None
        assert session.on_rgb(frame, sim_time_ns=11)["label"] == "metformin"

    def test_request_consumes_earliest_buffered_frame_after_park(self, meds, templates):
        """CON-5/TC-2: if RGB outruns the request/reply hops, buffer by sim
        stamp and select the earliest eligible frame, not a wall-dependent
        later arrival. Frames at the barrier remain ineligible."""
        session = self._session(meds, templates)
        at_barrier = camera_view_frame(meds, templates, "metformin")
        first_after = camera_view_frame(meds, templates, "omeprazole")
        later_after = camera_view_frame(meds, templates, "cetirizine")
        assert session.on_rgb(at_barrier, sim_time_ns=42) is None
        assert session.on_rgb(first_after, sim_time_ns=43) is None
        assert session.on_rgb(later_after, sim_time_ns=44) is None

        result = session.on_read_request(
            {"range_m": RANGE_M, "frame_after_sim_time_ns": 42},
            "ep-1/read0",
        )
        assert result["request_id"] == "ep-1/read0"
        assert result["label"] == "omeprazole"

    def test_refused_read_still_replies(self, meds, templates):
        """A tour must never hang on a bad view: the reply carries
        label null instead of being withheld."""
        session = self._session(meds, templates)
        session.on_read_request({"range_m": RANGE_M}, "ep-1/read2")
        result = session.on_rgb(np.full((240, 320, 3), 96, dtype=np.uint8))
        assert result["label"] is None
        assert result["request_id"] == "ep-1/read2"

    def test_reset_clears_the_pending_request(self, meds, templates):
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "metformin")
        assert session.on_rgb(frame, sim_time_ns=11) is None  # buffered before request
        session.on_read_request({"range_m": RANGE_M}, "ep-1/read0")
        session.on_reset_done()
        # Neither the pending request nor the pre-reset buffered frame may
        # cross the episode boundary.
        assert session.on_rgb(frame) is None
        assert (
            session.on_read_request(
                {"range_m": RANGE_M, "frame_after_sim_time_ns": 10}, "ep-2/read0"
            )
            is None
        )

    def test_zero_barrier_is_not_a_live_barrier(self, meds, templates):
        """PR #176 review: the executor defaults a missing park stamp to no
        key at all, never to 0 — because a 0 barrier is cleared by EVERY
        stamped frame, so an old buffered frame from during the park motion
        would answer the request. A 0 that reaches here anyway degrades to
        the unbarriered path (wait for the next frame), not to 'any stamped
        frame is eligible'."""
        session = self._session(meds, templates)
        stale = camera_view_frame(meds, templates, "metformin")
        fresh = camera_view_frame(meds, templates, "omeprazole")
        assert session.on_rgb(stale, sim_time_ns=5) is None  # buffered mid-park frame

        assert (
            session.on_read_request(
                {"range_m": RANGE_M, "frame_after_sim_time_ns": 0}, "ep-1/read0"
            )
            is None  # the stale buffered frame must NOT answer it
        )
        assert session.on_rgb(fresh, sim_time_ns=6)["label"] == "omeprazole"

    def test_malformed_barrier_degrades_instead_of_raising(self, meds, templates):
        """TC-2 trust boundary: a malformed barrier from an upstream node
        must not raise out of the reader's event loop (issue #160 item 1,
        same class). It degrades to the unbarriered path."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "cetirizine")
        assert (
            session.on_read_request(
                {"range_m": RANGE_M, "frame_after_sim_time_ns": "not-a-stamp"}, "ep-1/read0"
            )
            is None
        )
        assert session.on_rgb(frame, sim_time_ns=9)["label"] == "cetirizine"

    def test_sim_deadline_refuses_and_replies_with_no_rgb_events(self, meds, templates):
        """CON-5/TC-2 (PR #176 review): a silent or dead wrist producer
        emits zero frames, so the reply guarantee needs an independent
        contract-clock deadline rather than a delivered-frame count."""
        from aisle.nodes.label_reader import READ_TIMEOUT_SIM_NS

        session = self._session(meds, templates)
        session.on_read_request(
            {"range_m": RANGE_M, "frame_after_sim_time_ns": 1_000}, "ep-1/read0"
        )
        assert session.on_clock(1_000 + READ_TIMEOUT_SIM_NS) is None
        result = session.on_clock(1_000 + READ_TIMEOUT_SIM_NS + 1)
        assert result["request_id"] == "ep-1/read0"
        assert result["label"] is None  # refused, not guessed
        assert result["reason"] == "no_frame_after_park"
        assert session.pending is None  # and the session is free for the next read

    def test_unstamped_frame_stream_still_replies_on_sim_deadline(self, meds, templates):
        """Unstamped wrist frames cannot clear the barrier; the independent
        sim heartbeat still bounds the request."""
        from aisle.nodes.label_reader import READ_TIMEOUT_SIM_NS

        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "metformin")
        session.on_read_request({"range_m": RANGE_M, "frame_after_sim_time_ns": 10}, "r")
        assert session.on_rgb(frame) is None
        assert session.on_clock(10 + READ_TIMEOUT_SIM_NS + 1)["reason"] == "no_frame_after_park"

    def test_late_reset_does_not_clear_a_newer_episodes_request(self, meds, templates):
        """PR #176 review: reset_done and read_request arrive on independent
        queues. A reset delayed past the next episode's first request must
        NOT clear that live request — the state machine ignores replies it
        is not awaiting, so the dropped request would hang the tour with
        nothing downstream noticing. Sim time fences it."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "omeprazole")
        session.on_read_request({"range_m": RANGE_M, "frame_after_sim_time_ns": 900}, "ep-2/read0")

        session.on_reset_done(sim_time_ns=500)  # the PREVIOUS episode's reset, late

        assert session.pending is not None, "a live request was cleared by a stale reset"
        assert session.on_rgb(frame, sim_time_ns=901)["request_id"] == "ep-2/read0"

    def test_delayed_pre_reset_request_does_not_replace_live_request(self, meds, templates):
        """PR #176 review: the reverse independent-queue ordering is
        fenced too. After reset 500 and a live request at 900, a delayed
        old request at 100 is refused without overwriting the live one."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "omeprazole")
        session.on_reset_done(sim_time_ns=500)
        session.on_read_request({"range_m": RANGE_M, "frame_after_sim_time_ns": 900}, "new")

        stale = session.on_read_request(
            {"range_m": RANGE_M, "frame_after_sim_time_ns": 100}, "old-delayed"
        )

        assert stale["request_id"] == "old-delayed"
        assert stale["reason"] == "stale_read_request"
        assert session.pending["request_id"] == "new"
        assert session.on_rgb(frame, sim_time_ns=901)["request_id"] == "new"

    def test_reset_clears_a_request_from_the_ended_episode(self, meds, templates):
        """The fence cuts the other way too: a request barriered at or
        before the reset belongs to the episode that just ended."""
        session = self._session(meds, templates)
        frame = camera_view_frame(meds, templates, "omeprazole")
        session.on_read_request({"range_m": RANGE_M, "frame_after_sim_time_ns": 100}, "ep-1/read0")

        session.on_reset_done(sim_time_ns=500)

        assert session.pending is None
        assert session.on_rgb(frame, sim_time_ns=501) is None

    def test_reset_drops_pre_reset_frames_only(self, meds, templates):
        """Frames from before the reset show the previous episode's scene;
        frames after it are the new episode's and must survive."""
        session = self._session(meds, templates)
        old = camera_view_frame(meds, templates, "metformin")
        new = camera_view_frame(meds, templates, "cetirizine")
        assert session.on_rgb(old, sim_time_ns=400) is None
        session.on_reset_done(sim_time_ns=500)
        assert session.on_rgb(new, sim_time_ns=600) is None

        result = session.on_read_request(
            {"range_m": RANGE_M, "frame_after_sim_time_ns": 550}, "ep-2/read0"
        )
        assert result["label"] == "cetirizine"  # the post-reset frame answered it

    def test_no_calibration_no_read(self, meds, templates):
        session = ReaderSession(meds=meds, templates=templates)
        session.on_read_request({"range_m": RANGE_M}, "ep-1/read0")
        assert session.on_rgb(camera_view_frame(meds, templates, "metformin")) is None

    def test_detected_centre_is_used_over_frame_centre(self, meds, templates):
        session = self._session(meds, templates, find_centre=lambda rgb, prior: (200.0, 120.0))
        frame = camera_view_frame(meds, templates, "cetirizine", centre=(200, 120))
        session.on_read_request({"range_m": RANGE_M}, "r")
        assert session.on_rgb(frame)["label"] == "cetirizine"

    def test_rectified_path_is_taken_when_the_request_carries_a_camera_pose(self, meds, templates):
        """The executor's achieved-pose read: a fronto-parallel camera
        0.13 m from the face makes rectification equivalent to the
        centred crop — the synthetic frame must read through the
        rectified branch (find_centre must NOT be consulted)."""

        def exploding_find_centre(rgb, prior):
            raise AssertionError("rectified path must not use the detector")

        session = self._session(meds, templates, find_centre=exploding_find_centre)
        name = "omeprazole"
        frame = camera_view_frame(meds, templates, name)
        # camera: CV frame at (-0.13, 0, 0) from the face, +z toward it,
        # +x image-right = world -y, +y image-down... the synthetic frame
        # was built in the crop convention, so use the rot90 relation the
        # renderer realizes: view left = +y, view up = +z
        from aisle.verifier.calibration import lookat_rotation_cv

        face = np.array([0.5, 0.0, 0.2])
        cam_pos = face + [-RANGE_M, 0.0, 0.0]
        # a PERFECT read pose realizes exactly the look-at CV rotation
        # (wrist_camera_pose's GL_TO_CV factors cancel): the pinned
        # Genesis convention the synthetic frame is painted in
        cam_rot_cv = lookat_rotation_cv(cam_pos, face)
        session.on_read_request(
            {
                "range_m": RANGE_M,
                "face": face,
                "cam_pos": cam_pos,
                "cam_rot_cv": [float(v) for v in cam_rot_cv.reshape(-1)],
            },
            "r",
        )
        result = session.on_rgb(frame)
        assert result["label"] == name
        assert result["margin"] >= MARGIN_FLOOR

    def test_pitched_request_applies_the_raised_margin_floor(self, meds, templates, monkeypatch):
        """A pitched view can slide neighbour texture into the resample
        and read confidently wrong (+0.093 measured): the same margin
        that reads on a flat request must REFUSE on a pitched one."""
        from aisle.nodes import label_reader as module

        mid = (MARGIN_FLOOR + module.PITCHED_MARGIN_FLOOR) / 2
        fixed = module.ReadVerdict(label=None, margin=0.0, scores={"a": mid, "b": 0.0})

        def fake_rectified(*args, floor, **kwargs):
            return module.verdict_from_scores(fixed.scores, floor=floor)

        monkeypatch.setattr(module, "read_face_rectified", fake_rectified)
        base = {
            "range_m": RANGE_M,
            "face": [0.5, 0.0, 0.2],
            "cam_pos": [0.37, 0.0, 0.2],
            "cam_rot_cv": [float(v) for v in np.eye(3).reshape(-1)],
        }
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        session = self._session(meds, templates)
        session.on_read_request(dict(base), "flat")
        assert session.on_rgb(frame)["label"] == "a"
        session.on_read_request({**base, "pitched": True}, "pitched")
        assert session.on_rgb(frame)["label"] is None


class TestDetectBoxCentre:
    DETS = [
        {"label": "metformin", "score": 0.4, "box": [140, 100, 180, 140]},  # centred
        {"label": "ibuprofen", "score": 0.9, "box": [0, 60, 60, 200]},  # off-centre
        {"label": "amoxicillin", "score": 0.05, "box": [150, 110, 170, 130]},  # sub-floor
    ]

    def test_best_near_centre_detection_wins_and_labels_are_ignored(self, monkeypatch):
        """T2 no-color-prior: the color-worded detection is ONLY a
        centre; the off-centre 0.9 hit and the sub-floor hit both lose
        to the centred 0.4 one."""
        from aisle.verifier import models

        monkeypatch.setattr(models, "detect_meds", lambda rgb, names, model_pair: self.DETS)
        rgb = np.zeros((240, 320, 3), dtype=np.uint8)
        assert detect_box_centre(rgb, ["metformin"], None) == (160.0, 120.0)

    def test_no_qualifying_detection_returns_none(self, monkeypatch):
        from aisle.verifier import models

        monkeypatch.setattr(models, "detect_meds", lambda rgb, names, model_pair: self.DETS[1:])
        rgb = np.zeros((240, 320, 3), dtype=np.uint8)
        assert detect_box_centre(rgb, ["metformin"], None) is None
