"""TC-9 L1 conformance against the REAL built scene.

The unit tests pin the id map and the co-scheduling against the contract;
this test pins the whole L1 path against pinned Genesis itself — the only
thing that can say whether a pose ESTIMATED from published segmentation and
depth agrees with the ground truth L0 would have handed over for free. If it
does not, an L1 number measures the estimator's error, not the policy's.

Marker `sim`: imports genesis, runs headless (CON-12).
"""

import importlib.util

import numpy as np
import pytest

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None,
        reason="genesis not installed (uv sync --extra sim)",
    ),
]

# the estimator's own measured envelope (see segmented_pose's module docstring):
# 2.2 mm mean XY over 20 objects, worst unoccluded case ~2 mm. 10 mm is a
# regression gate with room for settling wobble, not a claim about accuracy.
MAX_XY_ERROR_M = 0.010


def test_l1_estimate_matches_genesis_ground_truth():
    """TC-9: seg + depth from ONE render pass, ids from the map the bridge
    publishes, pose ESTIMATED — and it lands on the ground-truth centre the
    L0 `poses` topic would have given away. Also pins the two facts a
    consumer depends on: the ids are the SCENE's map (not entity indices) and
    they survive the TC-1 narrowing to Int32."""
    from aisle.nodes.dora_genesis import realized_calibration, segmentation_id_map
    from aisle.nodes.segmented_pose import estimate_pose
    from aisle.scenes.pharmacy import build_scene, load_meds, load_physics, to_numpy
    from aisle.verifier.stages import backproject_overhead

    handle = build_scene(seed=3, embodiment="franka", n_envs=1, headless=True)
    meds = load_meds()
    calibration = realized_calibration(handle, load_physics(), is_store=False)

    _rgb, depth, seg, _ = handle.cams["overhead"].render(rgb=True, depth=True, segmentation=True)
    seg = np.asarray(seg, dtype=np.int32)  # TC-1: the wire type is the contract
    depth = np.asarray(depth, dtype=np.float32)
    id_map = segmentation_id_map(
        handle.scene.segmentation_idx_dict,
        {name: entity.idx for name, entity in handle.boxes.items()},
    )
    # the ids are genesis's own segmentation numbering, NOT entity indices —
    # the defect that made a spike mask robot links (TC-9)
    assert set(id_map) == set(handle.boxes)
    assert id_map != {name: [entity.idx] for name, entity in handle.boxes.items()}
    assert all(
        np.iinfo(np.int32).min < i < np.iinfo(np.int32).max for ids in id_map.values() for i in ids
    )

    def project(d, px):
        return backproject_overhead(d, calibration, px)

    errors = {}
    for name, entity in handle.boxes.items():
        size = meds[name]["size"]
        estimate = estimate_pose(
            seg, depth, id_map[name], float(size[2]), project, footprint_m=tuple(size[:2])
        )
        truth = np.asarray(to_numpy(entity.get_pos())).reshape(-1)[:3]
        errors[name] = float(np.linalg.norm(np.asarray(estimate["pos"][:2]) - truth[:2]))
        # z falls out exactly: the top surface plus half a known height IS the
        # centre, so it is held to the same gate as XY rather than a looser one
        assert abs(estimate["pos"][2] - truth[2]) < MAX_XY_ERROR_M, (name, estimate, truth)

    assert errors, "no meds in the scene to estimate"
    assert max(errors.values()) < MAX_XY_ERROR_M, errors


def test_segmentation_render_shares_the_depth_stamp_and_pass():
    """TC-9: one render pass yields both arrays at the same resolution, so a
    consumer can mask one and index the other. A seg/depth pair from two
    passes would measure a scene that never existed — the defect class that
    already reached the trace recorder and the realistic verifier."""
    from aisle.scenes.pharmacy import build_scene

    handle = build_scene(seed=4, embodiment="franka", n_envs=1, headless=True)
    out = handle.cams["overhead"].render(rgb=True, depth=True, segmentation=True)
    assert len(out) == 4  # genesis returns (rgb, depth, seg, normal)
    _, depth, seg, _ = out
    assert np.asarray(seg).shape == np.asarray(depth).shape
    # background is 0 and every non-zero id is in the scene's map (TC-9)
    ids = set(int(i) for i in np.unique(np.asarray(seg)))
    assert ids <= set(int(k) for k in handle.scene.segmentation_idx_dict)
