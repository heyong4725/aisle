"""world-model-env node v0 (next-phases §5.3; ADR-m3-protocol).

The environment ladder's cheapest tier: a DETERMINISTIC KINEMATIC
SURROGATE speaking the bridge's exact topic surface, so a graph swaps
environments (`dora-genesis` -> `world-model-env`) without edits.

v0 "cartoon physics", declared in the ADR: first-order joint tracking,
grasp-by-proximity attach/release, tray-settle on release, no contact,
no friction, no collision class. Scene layouts come from the SAME
seeded sampler the bridge uses, so per-seed geometry matches Genesis.
A learned backbone (Cosmos/DreamDojo-class) replaces the dynamics core
behind the same surface when the GPU budget lands.

Free-run only in v0 (the M3 population predates ADR-30); lockstep
participation is a recorded follow-up. CON-5: dynamics use no wall
clock and no unseeded RNG — same seed, same trajectory, bit-exact.
"""

from __future__ import annotations

import numpy as np

DT_NS = 10_000_000  # 100 Hz tick, the bridge's joint_state cadence
MAX_VEL = 1.0  # rad/s first-order tracking bound (bridge default)
ATTACH_R_M = 0.06  # TCP-to-box-centre attach radius (cartoon grasp)
ATTACH_Z_M = 0.10  # vertical window for the attach test
GRIP_CLOSE = 0.5  # gripper command threshold: above = closing
HOLD_DROP_M = 0.04  # held box rides this far below the TCP


def lag_step(current: np.ndarray, target: np.ndarray, dt_s: float, max_vel: float) -> np.ndarray:
    """One first-order tracking step, velocity-bounded per joint."""
    delta = np.clip(target - current, -max_vel * dt_s, max_vel * dt_s)
    return (current + delta).astype(np.float32)


def attach_candidate(
    tcp: np.ndarray, boxes: dict[str, np.ndarray], closing: bool, held: str | None
) -> str | None:
    """The cartoon grasp rule: while closing and empty-handed, the nearest
    box whose centre sits within ATTACH_R_M horizontally and ATTACH_Z_M
    vertically of the TCP attaches. Deterministic: ties break by med
    order (dict order = meds.toml order)."""
    if held is not None or not closing:
        return held
    best = None
    best_d = ATTACH_R_M
    for name, pose in boxes.items():
        dxy = float(np.hypot(pose[0] - tcp[0], pose[1] - tcp[1]))
        dz = abs(float(pose[2] - tcp[2]))
        if dxy <= best_d and dz <= ATTACH_Z_M:
            best, best_d = name, dxy
    return best


def rasterize_overhead(
    boxes: dict[str, np.ndarray],
    sizes: dict[str, tuple],
    seg_ids: dict[str, int],
    calibration: dict,
    resolution: tuple[int, int] = (640, 480),
) -> tuple[np.ndarray, np.ndarray]:
    """The cartoon's L1 sensor pair: (seg int32, depth float32) at the
    overhead camera, VER-8 conventions — each box's TOP FACE projected
    with the verifier's own projection and filled exactly, depth = the
    face's camera-frame z. Round-trip pinned by test: the REAL L1
    estimator recovers the box centre from this pair."""
    from aisle.verifier.stages import camera_frame_points, project_to_pixels

    w, h = resolution
    seg = np.zeros((h, w), dtype=np.int32)
    cam_h = float(calibration["overhead"]["cam_to_base"]["pos"][2])
    depth = np.full((h, w), cam_h, dtype=np.float32)
    for name, pose in boxes.items():
        sx, sy, sz = (float(v) for v in sizes[name])
        cx, cy, cz = (float(v) for v in pose[:3])
        top_z = cz + sz / 2
        corners = np.array(
            [
                [cx - sx / 2, cy - sy / 2, top_z],
                [cx + sx / 2, cy - sy / 2, top_z],
                [cx + sx / 2, cy + sy / 2, top_z],
                [cx - sx / 2, cy + sy / 2, top_z],
            ]
        )
        uv = project_to_pixels(corners, calibration)
        cam_z = float(camera_frame_points(corners, calibration)[:, 2].mean())
        mask = _fill_convex_quad(uv, h, w)
        seg[mask] = seg_ids[name]
        depth[mask] = cam_z
    return seg, depth


def _fill_convex_quad(uv: np.ndarray, h: int, w: int) -> np.ndarray:
    """Pure-numpy convex polygon fill (cv2 is not a locked dependency —
    measured missing from CI's unit env). A pixel is inside iff it sits
    on the same side of every edge of the CCW-ordered quad."""
    pts = np.asarray(uv, dtype=np.float64)
    centre = pts.mean(axis=0)
    order = np.argsort(np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0]))
    pts = pts[order]
    u0 = max(int(np.floor(pts[:, 0].min())), 0)
    u1 = min(int(np.ceil(pts[:, 0].max())) + 1, w)
    v0 = max(int(np.floor(pts[:, 1].min())), 0)
    v1 = min(int(np.ceil(pts[:, 1].max())) + 1, h)
    mask = np.zeros((h, w), dtype=bool)
    if u1 <= u0 or v1 <= v0:
        return mask
    us, vs = np.meshgrid(np.arange(u0, u1) + 0.0, np.arange(v0, v1) + 0.0)
    inside = np.ones(us.shape, dtype=bool)
    for i in range(len(pts)):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % len(pts)]
        cross = (bx - ax) * (vs - ay) - (by - ay) * (us - ax)
        inside &= cross >= 0
    mask[v0:v1, u0:u1] = inside
    return mask


def settle_pose(pose: np.ndarray, tray: dict, half_h: float) -> np.ndarray:
    """Release: a box let go inside the tray AABB settles upright on the
    tray floor; elsewhere it stays where it is (cartoon: no falling)."""
    tx, ty, tz = (float(v) for v in tray["pos"])
    hx, hy = float(tray["size"][0]) / 2, float(tray["size"][1]) / 2
    out = pose.copy()
    if abs(pose[0] - tx) <= hx and abs(pose[1] - ty) <= hy:
        out[2] = tz + float(tray["size"][2]) / 2 + half_h
        out[3:7] = (0.0, 0.0, 0.0, 1.0)
    return out


def main() -> None:  # pragma: no cover — graph-tested (M3 run)
    import json
    import os
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.nodes.ik_trajectory import fk_tcp
    from aisle.scenes.pharmacy import (
        load_meds,
        load_physics,
        resolve_layout,
        sample_placements,
    )
    from aisle.topics import stamp

    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    physics = load_physics()
    layout = resolve_layout(physics, embodiment)
    meds = load_meds()
    med_names = list(meds)
    n_arm = 7 if embodiment == "franka" else 6
    home = np.asarray(physics["embodiment"][embodiment]["home_qpos"], dtype=np.float32)[:n_arm]

    node = Node()
    seq: dict[str, int] = {}

    def send(topic: str, value, metadata: dict | None = None) -> None:
        seq[topic] = seq.get(topic, 0) + 1
        node.send_output(topic, value, stamp({**(metadata or {})}, seq[topic]))

    state = {
        "qpos": home.copy(),
        "target": home.copy(),
        "grip_target": 0.0,
        "grip": 0.0,
        "sim_ns": 0,
        "boxes": {},  # name -> pose7 float32
        "held": None,
        "tick": 0,
    }

    def do_reset(seed: int) -> None:
        placements = sample_placements(seed, med_names, layout)
        boxes: dict[str, np.ndarray] = {}
        for p in placements:
            pose = np.zeros(7, dtype=np.float32)
            pose[:3] = (p.x, p.y, p.z)
            pose[3:7] = (0.0, 0.0, 0.0, 1.0)
            boxes[p.name] = pose
        state.update(
            qpos=home.copy(),
            target=home.copy(),
            grip_target=0.0,
            grip=0.0,
            boxes=boxes,
            held=None,
        )

    def oracle_payload() -> np.ndarray:
        return np.concatenate([state["boxes"][n] for n in med_names]).astype(np.float32)

    do_reset(0)
    from aisle.scenes.pharmacy import wrist_mount_transform
    from aisle.verifier.calibration import build_calibration_v1

    cam_cfg = physics["cameras"]
    over_pos = list(cam_cfg["overhead_pos"])
    wrist_mount = wrist_mount_transform(cam_cfg, physics["embodiment"][embodiment])
    calibration = build_calibration_v1(
        overhead_pos=over_pos,
        overhead_lookat=list(cam_cfg["overhead_lookat"]),
        overhead_resolution=(640, 480),
        overhead_fov_deg=55.0,  # SCN-5 nominal
        wrist_offset_m=wrist_mount[:3, 3].tolist(),
        wrist_mount_rotation_gl=wrist_mount[:3, :3],
        wrist_resolution=(320, 240),
        wrist_fov_deg=70.0,  # SCN-5 nominal
    )
    seg_ids = {name: 10 + i for i, name in enumerate(med_names)}
    sizes = {name: tuple(meds[name]["size"]) for name in med_names}
    bridge_info = json.dumps(
        {
            "backend": "world-model-env-v0",
            "perception": "L1",
            "segmentation_ids": {k: [v] for k, v in seg_ids.items()},
            "calibration": calibration,
            "n_envs": 1,
        }
    )
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    sent_info = False

    for event in node:
        if event["type"] != "INPUT":
            continue
        eid = event["id"]
        metadata = event.get("metadata") or {}
        if eid == "joint_cmd":
            cmd = np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.float32)
            state["target"] = cmd[:n_arm]
        elif eid == "gripper_cmd":
            state["grip_target"] = float(
                np.asarray(event["value"].to_numpy(zero_copy_only=False)).reshape(-1)[0]
            )
        elif eid == "reset":
            payload = np.asarray(event["value"].to_numpy(zero_copy_only=False)).reshape(-1)
            reset_seed = int(payload[0])
            if not metadata.get("request_id"):
                raise ValueError("reset request missing request_id metadata (TC-6)")
            do_reset(reset_seed)
            send(
                "reset_done",
                pa.array(np.array([1], dtype=np.uint32)),
                {
                    "sim_time_ns": state["sim_ns"],
                    "env_id": 0,
                    "request_id": metadata.get("request_id", ""),
                    "seed": reset_seed,
                    "mode": 0,
                    "t_reset_ms": 0,
                },
            )
        elif eid == "tick":
            state["tick"] += 1
            state["sim_ns"] += DT_NS
            dt_s = DT_NS / 1e9
            state["qpos"] = lag_step(state["qpos"], state["target"], dt_s, MAX_VEL)
            state["grip"] = float(
                lag_step(
                    np.array([state["grip"]], dtype=np.float32),
                    np.array([state["grip_target"]], dtype=np.float32),
                    dt_s,
                    4.0,
                )[0]
            )
            tcp = fk_tcp(state["qpos"][:n_arm], embodiment)
            closing = state["grip_target"] > GRIP_CLOSE
            was_held = state["held"]
            state["held"] = attach_candidate(tcp, state["boxes"], closing, state["held"])
            if state["held"] is not None and not closing:
                # release: settle in place (tray floor if inside the tray)
                name = state["held"]
                half_h = float(meds[name]["size"][2]) / 2
                state["boxes"][name] = settle_pose(state["boxes"][name], layout["tray"], half_h)
                state["held"] = None
            elif state["held"] is not None:
                name = state["held"]
                pose = state["boxes"][name]
                pose[:3] = (tcp[0], tcp[1], tcp[2] - HOLD_DROP_M)
                if was_held is None:
                    print(f"[wm-env] attached {name}", file=sys.stderr)
            meta = {"sim_time_ns": state["sim_ns"], "env_id": 0}
            fingers = np.array([state["grip"], state["grip"]], dtype=np.float32)
            send("joint_state", pa.array(np.concatenate([state["qpos"], fingers])), meta)
            send("gripper_state", pa.array(np.array([state["grip"]], dtype=np.float32)), meta)
            if not sent_info:
                send("bridge_info", pa.array([bridge_info]), meta)
                sent_info = True
            if state["tick"] % 3 == 0:
                payload = oracle_payload()
                send("oracle_state", pa.array(payload), meta)
                send("poses", pa.array(payload), meta)
            if state["tick"] % 7 == 0:
                fmeta = {**meta, "h": 8, "w": 8}
                send("rgb_overhead", pa.array(frame.reshape(-1)), fmeta)
                send("rgb_wrist", pa.array(frame.reshape(-1)), fmeta)
                seg, depth = rasterize_overhead(state["boxes"], sizes, seg_ids, calibration)
                smeta = {**meta, "h": 480, "w": 640}
                send("seg_overhead", pa.array(seg.reshape(-1)), smeta)
                send("depth_overhead", pa.array(depth.reshape(-1)), smeta)


if __name__ == "__main__":
    main()
