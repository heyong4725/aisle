"""L1 perception: pose ESTIMATED from ground-truth segmentation (TC-9).

The perception ladder's middle rung. L0 hands the graph `poses` — the
non-privileged ground-truth pose — so grounding is free and only grasping is
exercised. L1 forbids `poses` and gives the graph `seg_overhead` +
`depth_overhead` instead: the mask says WHICH pixels are the object (still
ground truth, which is what makes this a rung and not L2), and the pose is
estimated from those pixels. That is the first tier where a wrong estimate
can lose an episode.

Estimation is deliberately the simplest thing that measures well: mask the
target's pixels, back-project them with the published calibration, take the
top surface, and place the centre half a box-height below it. Measured
against Genesis ground truth over 20 objects on 4 seeds: **2.2 mm mean XY
error, z error ~0** (z falls out exactly, since the top surface plus half a
known height IS the centre).

The failure mode that matters is occlusion, not noise: a partially hidden box
gives a centroid biased toward its visible fragment. The two worst cases in
that sweep were the two smallest masks (19.5 mm and 9.0 mm) while every
unoccluded object came in under ~2 mm. So an estimate carries the mask size
that produced it and the node REFUSES below a floor rather than publishing a
confident guess — the same lesson the identity stage learned from tray-sized
detections.
"""

from __future__ import annotations

import numpy as np

# below this many mask pixels the centroid is dominated by whatever fragment
# is visible. Unoccluded meds render 433-970 px at 640x480 from the overhead
# camera, so this floor rejects a box more than roughly half hidden while
# never rejecting a clear one (measured, seeds 3-6).
MIN_MASK_PIXELS = 200
# the top surface is the highest depth band of the mask: edge pixels straddle
# the silhouette and pick up the shelf behind, exactly as VER-10's
# dominant_surface found for the verifier's own masks
TOP_SURFACE_QUANTILE = 0.85


class PoseRefused(Exception):
    """The mask cannot support a trustworthy pose (TC-9). Refusing is the
    contract: a biased centroid is worse than no pose, because downstream the
    grasp planner cannot tell the difference."""


def seg_ids_for(id_map: dict, target: str) -> list[int]:
    """The `seg_overhead` ids belonging to one med, from the map the bridge
    publishes in `bridge_info`.

    Never derive these from entity indices: Genesis's segmentation ids are its
    own map (seg id -> (entity_idx, link_idx)), so masking on entity index
    selects other geometry. A spike that did exactly that returned robot links
    with pixel counts identical across scenes whose layouts differed (TC-9)."""
    ids = id_map.get(target)
    if not ids:
        raise PoseRefused(f"no segmentation ids published for {target!r} (TC-9 bridge_info)")
    return [int(i) for i in ids]


def estimate_pose(
    seg: np.ndarray,
    depth: np.ndarray,
    ids: list[int],
    box_height_m: float,
    backproject,
    min_mask_pixels: int = MIN_MASK_PIXELS,
) -> dict:
    """(x, y, z) of the box CENTRE plus the evidence that produced it.

    `backproject(depth, pixels) -> (N, 3)` base-frame points is injected so
    this stays pure: the caller passes the VER-8 calibration already bound.
    Returns {pos, mask_pixels, top_surface_z_m} — the mask size travels with
    the estimate so a consumer can weigh it (TC-9)."""
    mask = np.isin(seg, ids)
    pixels = int(mask.sum())
    if pixels < min_mask_pixels:
        raise PoseRefused(
            f"mask is {pixels} px, under the {min_mask_pixels} px floor — a centroid this "
            "small is dominated by the visible fragment of an occluded box (TC-9)"
        )
    vs, us = np.nonzero(mask)
    points = np.asarray(backproject(depth, np.stack([us, vs], axis=1)), dtype=np.float64)
    if not np.all(np.isfinite(points)):
        raise PoseRefused("back-projected mask contains non-finite points")
    top = points[points[:, 2] >= np.quantile(points[:, 2], TOP_SURFACE_QUANTILE)]
    top_z = float(np.median(top[:, 2]))
    return {
        "pos": [float(top[:, 0].mean()), float(top[:, 1].mean()), top_z - box_height_m / 2],
        "mask_pixels": pixels,
        "top_surface_z_m": top_z,
    }


def main() -> None:  # pragma: no cover — dora runtime
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import load_meds
    from aisle.verifier.stages import backproject_overhead

    meds = load_meds()
    node = Node()
    calibration: dict | None = None
    id_map: dict = {}
    target: str | None = None
    latest_depth: np.ndarray | None = None

    for event in node:
        if event["type"] != "INPUT":
            continue
        topic, metadata = event["id"], (event.get("metadata") or {})
        if topic == "bridge_info":
            info = json.loads(event["value"][0].as_py())
            calibration = info["calibration"]
            id_map = info.get("segmentation_ids") or {}
        elif topic == "target_request":
            target = json.loads(event["value"][0].as_py())["target_med"]
        elif topic == "depth_overhead":
            h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
            latest_depth = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(h, w)
        elif topic == "seg_overhead" and target and calibration and latest_depth is not None:
            h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
            seg = np.asarray(event["value"].to_numpy(zero_copy_only=False)).reshape(h, w)

            # bind the calibration explicitly: a closure over the loop
            # variable would follow a later bridge_info republish (B023)
            def project(d, px, cal=calibration):
                return backproject_overhead(d, cal, px)

            try:
                estimate = estimate_pose(
                    seg,
                    latest_depth,
                    seg_ids_for(id_map, target),
                    float(meds[target]["size"][2]),
                    project,
                )
            except PoseRefused as exc:
                # TC-9: refuse rather than publish a biased pose. The episode
                # then closes honestly on the verifier's timeout instead of the
                # arm diving at a phantom.
                print(f"L1 pose refused for {target}: {exc}", file=sys.stderr)
                continue
            node.send_output(
                "object_pose",
                pa.array(np.asarray(estimate["pos"] + [0.0, 0.0, 0.0, 1.0], dtype=np.float32)),
                metadata={
                    **{k: metadata[k] for k in ("sim_time_ns", "env_id") if k in metadata},
                    "target_med": target,
                    "mask_pixels": estimate["mask_pixels"],
                    "perception": "L1",
                },
            )


if __name__ == "__main__":  # pragma: no cover
    main()
