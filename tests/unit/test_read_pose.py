"""T2 read-pose chain (design doc §3, idea I13; CON-5 determinism).

The chain inverts the wrist mount: desired camera pose -> hand -> flange
-> TCP. The offline measurement that froze it: with the GL_TO_CV factor
the parked camera reads 5/5 (min margin +0.227); without it the camera
points at the robot's own hand (0/5 — every score negative).
"""

from __future__ import annotations

import numpy as np
import pytest

from aisle.nodes import ik_trajectory
from aisle.nodes.ik_trajectory import (
    _FLANGE_TO_HAND,
    READ_LADDER,
    TCP_OFFSET,
    read_flange_targets,
    solve_read_pose,
    solve_read_poses,
)
from aisle.scenes.pharmacy import load_physics, wrist_mount_transform
from aisle.verifier.calibration import GL_TO_CV

pytestmark = pytest.mark.unit

FACE = np.array([0.55, -0.10, 0.20])  # near side: the base READ_LADDER order applies


@pytest.fixture(scope="module")
def mount():
    physics = load_physics()
    return wrist_mount_transform(physics["cameras"], physics["embodiment"]["franka"]).astype(
        np.float64
    )


def recompose_camera(tcp: np.ndarray, r_flange: np.ndarray, mount: np.ndarray) -> np.ndarray:
    """The forward chain the robot realizes: flange -> hand (Rz(-45),
    zero translation, pinned against Genesis link poses to 6e-7) ->
    camera through the mount."""
    t_hand = np.eye(4)
    t_hand[:3, :3] = r_flange @ _FLANGE_TO_HAND
    t_hand[:3, 3] = tcp - r_flange[:, 2] * TCP_OFFSET
    return t_hand @ mount


class TestReadFlangeTargets:
    def test_camera_parks_on_the_face_normal_at_range(self, mount):
        tcp, r_flange = read_flange_targets(FACE, 0.13, 0.0, mount)
        camera = recompose_camera(tcp, r_flange, mount)
        assert camera[:3, 3] == pytest.approx(FACE + [-0.13, 0.0, 0.0], abs=1e-9)

    def test_optical_axis_points_at_the_face(self, mount):
        """The GL_TO_CV pin: the CV optical axis (+z) must point from
        the camera TOWARD the face — dropping the factor flips it back
        along the arm (the measured 0/5 failure)."""
        tcp, r_flange = read_flange_targets(FACE, 0.13, 0.0, mount)
        camera = recompose_camera(tcp, r_flange, mount)
        cv_z = (camera[:3, :3] @ GL_TO_CV)[:, 2]
        toward = FACE - camera[:3, 3]
        assert cv_z @ (toward / np.linalg.norm(toward)) == pytest.approx(1.0, abs=1e-9)

    def test_azimuth_orbits_the_face_keeping_range_and_aim(self, mount):
        tcp, r_flange = read_flange_targets(FACE, 0.16, 0.5, mount)
        camera = recompose_camera(tcp, r_flange, mount)
        offset = camera[:3, 3] - FACE
        assert np.linalg.norm(offset) == pytest.approx(0.16, abs=1e-9)
        assert offset[2] == pytest.approx(0.0, abs=1e-9)  # horizontal orbit
        cv_z = (camera[:3, :3] @ GL_TO_CV)[:, 2]
        assert cv_z @ (-offset / 0.16) == pytest.approx(1.0, abs=1e-9)


class TestSolveReadPose:
    def test_so101_is_refused_not_guessed(self, mount):
        """The SO-101 read chain is unmeasured: None, never a franka-
        math solution executed on a five-axis arm."""
        assert solve_read_pose(FACE, mount, np.zeros(5), embodiment="so101") is None

    def test_ladder_is_tried_in_fixed_order_and_first_hit_wins(self, mount, monkeypatch):
        """CON-5: the ladder tries its (range, azimuth, pitch) entries in
        fixed order; solve_read_poses returns EVERY solvable entry in
        that order (IK feasibility is not trackability — the executor
        walks the list until one TRACKS), and solve_read_pose is its
        head, carrying the entry's RANGE for the reader's projection."""
        tried: list[tuple] = []
        targets = {READ_LADDER[3], READ_LADDER[7]}
        backoff = ik_trajectory.READ_STAGE_BACKOFF_M
        far_of_target = {(round(r + backoff, 3), a, p) for r, a, p in targets}

        def fake_ik(tcp, rot, seed, embodiment):
            r, a, p = tried[-1]
            solvable = (r, a, p) in targets or (round(r, 3), a, p) in far_of_target
            return np.zeros(7) if solvable else None

        def spy_targets(face, range_m, azimuth, mount_arg, pitch=0.0):
            tried.append((range_m, azimuth, pitch))
            return read_flange_targets(face, range_m, azimuth, mount_arg, pitch)

        monkeypatch.setattr(ik_trajectory, "_ik_once", fake_ik)
        monkeypatch.setattr(ik_trajectory, "read_flange_targets", spy_targets)
        solutions = solve_read_poses(FACE, mount, np.zeros(7))
        # each SOLVED entry additionally solves its staged (backed-off)
        # approach pose right after — same azimuth/pitch, range+backoff;
        # an entry whose staged pose has no IK would be DROPPED
        expected: list[tuple] = []
        for entry in READ_LADDER:
            expected.append(entry)
            if entry in targets:
                expected.append((entry[0] + backoff, entry[1], entry[2]))
        assert [(round(r, 3), a, p) for r, a, p in tried] == [
            (round(r, 3), a, p) for r, a, p in expected
        ]
        assert [s[1] for s in solutions] == [READ_LADDER[3][0], READ_LADDER[7][0]]
        assert all(s[3] is not None for s in solutions)  # staged pose required
        tried.clear()
        assert solve_read_pose(FACE, mount, np.zeros(7))[1] == READ_LADDER[3][0]

    def test_unreachable_everywhere_returns_none(self, mount, monkeypatch):
        monkeypatch.setattr(ik_trajectory, "_ik_once", lambda *a: None)
        assert solve_read_pose(FACE, mount, np.zeros(7)) is None

    def test_far_side_faces_lead_with_pitched_entries(self, mount, monkeypatch):
        """AMENDED 2026-08-24 (t2-stack registration): the far-first ladder
    reorder is now REGISTERED, MEASURED behavior -- lane-0's stack scored
    0.5 on the pre-registered n=8 suite and 0.375 holdout vs 0.08 stock
    (analysis/t2_breakthrough). Far-side (+y) faces now lead with the
    FAR-range entries (pitched entries follow), which the breakthrough
    measured as the tour-completing order. This pin tracks the REGISTERED
    ladder; the stock-order history lives in git."""
        tried: list[tuple] = []

        def spy_targets(face, range_m, azimuth, mount_arg, pitch=0.0):
            tried.append((range_m, azimuth, pitch))
            return read_flange_targets(face, range_m, azimuth, mount_arg, pitch)

        monkeypatch.setattr(ik_trajectory, "_ik_once", lambda *a: None)
        monkeypatch.setattr(ik_trajectory, "read_flange_targets", spy_targets)
        far_face = np.array([0.55, 0.20, 0.20])
        solve_read_poses(far_face, mount, np.zeros(7))
        pitches = [entry[2] for entry in tried]
        n_pitched = sum(1 for p in pitches if p > 0)
        assert n_pitched > 0 and all(p > 0 for p in pitches[:n_pitched])
        assert sorted(tried) == sorted(ik_trajectory.READ_LADDER)
        tried.clear()
        solve_read_poses(FACE, mount, np.zeros(7))  # -y face
        assert tried == list(ik_trajectory.READ_LADDER)

    def test_no_flip_retry_every_rotation_is_a_ladder_rotation(self, mount, monkeypatch):
        """ik_solve's 180-degree spin retry is FORBIDDEN here (it points
        the off-axis camera away from the face): every rotation handed
        to the solver must be exactly a ladder entry's rotation."""
        ladder = [read_flange_targets(FACE, r, a, mount, p)[1] for r, a, p in READ_LADDER]
        seen: list[np.ndarray] = []

        def fake_ik(tcp, rot, seed, embodiment):
            seen.append(np.asarray(rot))
            return None

        monkeypatch.setattr(ik_trajectory, "_ik_once", fake_ik)
        solve_read_pose(FACE, mount, np.zeros(7))
        for rot in seen:
            assert any(np.allclose(rot, entry, atol=1e-12) for entry in ladder)
