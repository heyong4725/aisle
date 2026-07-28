"""s1-driver-v2: agent-iterated S1 driver (campaign H3, ideas I2+).

Derived from s1_expert.py (T15) with three fixes measured from the
baseline run 20260728-001009-b4d1fd (pass1 0.125, 5 timeout / 2
extra_item on seeds 0..7):

1. COUNTER RE-PARK PER PLACEMENT. The expert spread drops laterally in
   the base frame (dy = placed*0.12); the third drop (dy 0.24) failed
   `IK failed: transfer` in EVERY episode that needed it, so any >=3-unit
   order timed out. v2 instead re-parks the base at a per-placement park
   pose along the counter and always drops at the proven base-frame
   sweet spot (0.50, 0.0).
2. L0 PICKS ARE SKIPPED AT PLAN TIME. A top-down descent to a bottom
   -level slot jams the wrist assembly on the L1 board (joint-5 track
   err ~1.0 rad from pregrasp on) and the blind close snags a NEIGHBOUR
   box which then gets delivered -> `extra_item`, the 10x class. Offline
   IK prototyping (front grasp, flip ladders) found no in-envelope
   chain, so v2 refuses those picks: a missing item is a plain fail; a
   wrong item on the counter is 10x worse.
3. NO DEADLOCK IDLING. A nav failure after retries now proceeds into
   the settle/verify path (bounded re-parks) instead of idling to the
   episode timeout; a pick whose grasp-critical stages (pregrasp/
   advance/close) bailed releases at the carry tuck and skips its
   goto+place pair instead of delivering an untrusted grasp.

Pure planning/geometry at module level (CON-12); dora only in main().
"""

from __future__ import annotations

import math

import numpy as np

from aisle.nodes.budget_guard import fk_flange
from aisle.nodes.grasp_topdown import plan_grasp
from aisle.nodes.ik_trajectory import (
    STAGING_Z,
    Stage,
    fk_tcp,
    ik_continuation,
    ik_solve,
    quat_to_rotation,
    topdown_rotation,
)
from aisle.scenes.pharmacy import load_meds, load_physics
from aisle.scenes.store import load_planogram, slot_world_pose, stocked_items

PARK_STANDOFF_M = 0.48
# the ONLY drop point v2 uses, in the base frame: the (0.50, ~0) spot
# succeeded in every baseline episode; lateral spread comes from the
# BASE park, not the arm (fix 1)
COUNTER_DROP_X = 0.50
# world-y park offsets along the counter per placement index; the drop
# lands at the park's y (base yaw pi maps base-frame (0.50, 0) to
# world (park_x - 0.50, park_y)). 0.15 spacing keeps worst-case
# capture-tol drift (0.075 m) from stacking adjacent boxes; the counter
# is 0.9 m wide so |y| <= 0.30 + 0.075 stays well inside the footprint
# (+0.02 margin, RS-7). A 6th unit reuses 0.075 — the least-bad slot.
PLACE_PARK_YS = (0.0, 0.15, -0.15, 0.30, -0.30, 0.075)
GRASP_CRITICAL = frozenset({"pregrasp", "advance", "close"})


def park_pose_for_slot(plano: dict, slot_id: str) -> list[float]:
    """Store-frame [x, y, yaw] parking the base PARK_STANDOFF_M in front
    of the slot, facing the unit (yaw = unit facing + pi)."""
    world, unit_yaw = slot_world_pose(plano, slot_id)
    return [
        world[0] + PARK_STANDOFF_M * math.cos(unit_yaw),
        world[1] + PARK_STANDOFF_M * math.sin(unit_yaw),
        _wrap(unit_yaw + math.pi),
    ]


def place_park_pose(counter_loc: list[float], placed: int) -> list[float]:
    """Per-placement park pose: the counter location shifted along the
    counter's width axis (perpendicular to its facing)."""
    x, y, yaw = counter_loc
    off = PLACE_PARK_YS[min(placed, len(PLACE_PARK_YS) - 1)]
    # shift perpendicular to the facing direction
    return [x - off * math.sin(yaw), y + off * math.cos(yaw), yaw]


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def slot_level(slot_id: str) -> int:
    """Level index from a slot id like 'A1-L0-S2'."""
    return int(slot_id.split("-L")[1].split("-")[0])


def filter_plan(subtasks: list[dict]) -> tuple[list[dict], int]:
    """Fix 2: drop every goto/pick/goto/place quad whose pick sources a
    bottom-level (L0) slot — kinematically infeasible top-down and an
    extra_item hazard. Returns (kept, dropped_quads). Falls back to the
    unfiltered list if the plan does not group into quads."""
    if len(subtasks) % 4 != 0:
        return subtasks, 0
    kept: list[dict] = []
    dropped = 0
    for i in range(0, len(subtasks), 4):
        quad = subtasks[i : i + 4]
        ops = [s.get("op") for s in quad]
        if ops != ["goto", "pick", "goto", "place"]:
            return subtasks, 0
        if slot_level(quad[1]["slot"]) == 0:
            dropped += 1
            continue
        kept += quad
    return kept, dropped


def to_base_frame(p_store, base_pose) -> list[float]:
    """Store-frame point -> base frame (the arm's frame, ADR-13)."""
    bx, by, byaw = float(base_pose[0]), float(base_pose[1]), float(base_pose[2])
    dx, dy = float(p_store[0]) - bx, float(p_store[1]) - by
    cos_y, sin_y = math.cos(-byaw), math.sin(-byaw)
    return [dx * cos_y - dy * sin_y, dx * sin_y + dy * cos_y, float(p_store[2])]


def _yaw_quat_xyzw(yaw: float) -> np.ndarray:
    half = yaw / 2
    return np.array([0.0, 0.0, math.sin(half), math.cos(half)], dtype=np.float32)


def pick_stages(
    item_pos_base, item_yaw_base: float, size_xyz, home: np.ndarray, counter_top_z: float
) -> tuple[list[Stage] | None, np.ndarray | None, float, str | None]:
    """The split PICK half (unchanged from s1_expert: rise/staging/
    pregrasp/advance/close/lift/retract + gentle carry tuck)."""
    folded_yaw = ((item_yaw_base + math.pi / 2) % math.pi) - math.pi / 2
    target_pose = np.concatenate(
        [np.asarray(item_pos_base, dtype=np.float32), _yaw_quat_xyzw(folded_yaw)]
    )
    grasp, approach, place_z = plan_grasp(
        target_pose, size_xyz, front=False, tray_top_z=counter_top_z
    )
    grasp_pos = grasp[:3].astype(np.float64)
    grasp_rot = quat_to_rotation(grasp[3:7])
    home_arm = np.asarray(home, dtype=np.float32)[:7]
    pre_pos = grasp_pos - grasp_rot[:, 2] * approach
    up = np.array([0.0, 0.0, 0.015])

    home_tcp = fk_tcp(home_arm)
    staging_z = max(STAGING_Z, float(pre_pos[2]))
    rise_pos = np.array([home_tcp[0], home_tcp[1], staging_z])
    staging_pos = np.array([pre_pos[0], pre_pos[1], staging_z])
    q_rise = ik_solve(rise_pos, grasp_rot, home_arm)
    if q_rise is None:
        return None, None, place_z, "IK failed: rise"
    staging_path = ik_continuation(rise_pos, staging_pos, grasp_rot, q_rise)
    if staging_path is None:
        return None, None, place_z, "IK failed: staging"
    pregrasp_path = ik_continuation(staging_pos, pre_pos, grasp_rot, staging_path[-1])
    if pregrasp_path is None:
        return None, None, place_z, "IK failed: pregrasp"
    advance_path = ik_continuation(pre_pos, grasp_pos, grasp_rot, pregrasp_path[-1])
    if advance_path is None:
        return None, None, place_z, "IK failed: advance"
    lift_path = ik_continuation(grasp_pos, grasp_pos + up, grasp_rot, advance_path[-1])
    if lift_path is None:
        return None, None, place_z, "IK failed: lift"
    retract_path = ik_continuation(grasp_pos + up, pre_pos + up, grasp_rot, lift_path[-1])
    if retract_path is None:
        return None, None, place_z, "IK failed: retract"
    carry_pos = np.array([home_tcp[0], home_tcp[1], staging_z])
    carry_path = ik_continuation(pre_pos + up, carry_pos, grasp_rot, retract_path[-1])
    if carry_path is None:
        return None, None, place_z, "IK failed: carry"
    stages = [
        Stage("rise", (q_rise,), 0.0, 0.1),
        Stage("staging", tuple(staging_path), 0.0, 0.1),
        Stage("pregrasp", tuple(pregrasp_path), 0.0, 0.4, track_tol=0.05),
        Stage("advance", tuple(advance_path), 0.0, 0.5, vel=0.5, track_tol=0.03),
        Stage("close", (advance_path[-1],), 1.0, 0.6, track_tol=0.03),
        Stage("lift", tuple(lift_path), 1.0, 0.2, vel=0.5),
        Stage("retract", tuple(retract_path), 1.0, 0.2, vel=0.5),
        Stage("carry", tuple(carry_path), 1.0, 0.3, vel=0.35),
    ]
    return stages, carry_path[-1], place_z, None


def rotation_to_quat_of(q_arm: np.ndarray):
    """TC-1 quat of the flange rotation at q (shared franka DH)."""
    from aisle.nodes.ik_trajectory import rotation_to_quat

    return rotation_to_quat(fk_flange(np.asarray(q_arm, dtype=np.float64))[1])


def place_stages(
    q_start: np.ndarray, drop_xy, place_tcp_z: float, home: np.ndarray
) -> tuple[list[Stage] | None, str | None]:
    """The split PLACE half at the counter (unchanged from s1_expert)."""
    home_arm = np.asarray(home, dtype=np.float32)[:7]
    q_start = np.asarray(q_start, dtype=np.float32)[:7]
    start_tcp = fk_tcp(q_start)
    rot_start = quat_to_rotation(rotation_to_quat_of(q_start))
    yaw_cur = math.atan2(rot_start[1, 0], rot_start[0, 0])
    q_unwind = q_start.copy()
    best = None
    for residual in (0.0, math.pi, -math.pi):
        j7 = q_start[6] - (yaw_cur - residual)
        if -2.8973 <= j7 <= 2.8973:
            if best is None or abs(j7 - q_start[6]) < abs(best[0] - q_start[6]):
                best = (j7, residual)
    if best is None:
        return None, "unwind: no in-limit wrist spin"
    q_unwind[6] = best[0]
    place_rot = topdown_rotation(best[1])
    transfer_z = place_tcp_z + 0.06
    transfer_pos = np.array([drop_xy[0], drop_xy[1], transfer_z])
    lower_pos = np.array([drop_xy[0], drop_xy[1], place_tcp_z])
    transfer_path = ik_continuation(start_tcp, transfer_pos, place_rot, q_unwind)
    if transfer_path is None:
        return None, "IK failed: transfer"
    lower_path = ik_continuation(transfer_pos, lower_pos, place_rot, transfer_path[-1])
    if lower_path is None:
        return None, "IK failed: lower"
    stages = [
        Stage("unwind", (q_unwind,), 1.0, 0.2, vel=0.5),
        Stage("transfer", tuple(transfer_path), 1.0, 0.3, vel=0.35),
        Stage("lower", tuple(lower_path), 1.0, 1.0, vel=0.35, track_tol=0.03),
        Stage("release", (lower_path[-1],), 0.0, 1.5, vel=0.35, track_tol=0.03),
        Stage("clear", (transfer_path[-1],), 0.0, 0.1),
        Stage("home", (home_arm,), 0.0, 0.0),
    ]
    return stages, None


def main() -> None:
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.nodes.ik_trajectory import StageStreamer
    from aisle.topics import make_sender

    physics = load_physics()
    profile = physics["embodiment"]["mobile"]
    home = np.asarray(profile["home_qpos"], dtype=np.float32)
    meds = load_meds()
    plano = load_planogram()
    from aisle.mobility.nav import load_locations, load_nav_params

    locations = load_locations()
    nav_params = load_nav_params("mobile")
    counter_top = plano["store"]["counter_pos"][2] + plano["store"]["counter_size"][2] / 2
    dt = 0.01

    node = Node()
    send = make_sender(node)

    goal = None
    roster: list[str] = []
    queue: list[dict] = []
    pending: dict | None = None
    settling: dict | None = None
    settle_window: list[list[float]] = []
    streamer: StageStreamer | None = None
    after_stream: dict | None = None
    stream_bails: set[str] = set()
    base_pose = [0.0, 0.0, 0.0]
    latest_poses: np.ndarray | None = None
    carry_q: np.ndarray | None = None
    carry_place_z = 0.0
    placed = 0
    nav_seq = 0

    def clear() -> None:
        nonlocal goal, roster, queue, pending, streamer, after_stream
        nonlocal carry_q, placed, latest_poses, settling, settle_window, stream_bails
        goal = None
        roster = []
        queue = []
        pending = None
        settling = None
        settle_window = []
        streamer = None
        after_stream = None
        stream_bails = set()
        carry_q = None
        placed = 0
        latest_poses = None

    def send_nav(nav_goal: dict) -> None:
        nonlocal nav_seq
        nav_seq += 1
        send("nav_goal", pa.array([json.dumps(nav_goal)]), {"goal_id": f"nav-{nav_seq:03d}"})

    def start_pick(slot_id: str, category: str) -> None:
        nonlocal streamer, carry_q, carry_place_z, after_stream, stream_bails
        item_id = f"{slot_id}#0"
        if item_id not in roster or latest_poses is None:
            print(f"pick aborted: no pose for {item_id}", file=sys.stderr)
            skip_pick_pair()
            return
        idx = roster.index(item_id)
        block = latest_poses[idx * 7 : idx * 7 + 7]
        pos_base = to_base_frame(block[:3], base_pose)
        x, y, z, w = (float(v) for v in block[3:7])
        item_yaw_store = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        yaw_base = _wrap(item_yaw_store - base_pose[2])
        stages, q_carry, place_z, err = pick_stages(
            pos_base, yaw_base, meds[category]["size"], home, counter_top
        )
        if err:
            print(f"pick {item_id} failed: {err}", file=sys.stderr)
            skip_pick_pair()
            return
        streamer = StageStreamer(stages, home, dt, 1.0, integ_cap=0.30)
        stream_bails = set()
        carry_q = q_carry
        carry_place_z = place_z
        after_stream = {"kind": "pick"}
        print(f"picking {item_id} from {slot_id}", file=sys.stderr)

    def skip_pick_pair() -> None:
        """Fix 3: a failed/untrusted pick skips its goto+place partners
        instead of 'placing' nothing (or worse, a snagged neighbour)."""
        nonlocal carry_q
        carry_q = None
        if len(queue) >= 2 and queue[0].get("op") == "goto" and queue[1].get("op") == "place":
            del queue[:2]
            print("skipping goto+place for failed pick", file=sys.stderr)
        advance()

    def abort_pick() -> None:
        """Grasp-critical stage bailed: release whatever the fingers hold
        at the carry tuck (NOT over the counter) and go home."""
        nonlocal streamer, after_stream, carry_q, stream_bails
        home_arm = np.asarray(home, dtype=np.float32)[:7]
        release_q = carry_q if carry_q is not None else home_arm
        stages = [
            Stage("abort_release", (np.asarray(release_q, dtype=np.float32),), 0.0, 1.2),
            Stage("home", (home_arm,), 0.0, 0.0),
        ]
        streamer = StageStreamer(stages, home, dt, 1.0, integ_cap=0.30)
        stream_bails = set()
        after_stream = {"kind": "abort"}
        print("pick aborted: grasp-critical stage bailed; releasing", file=sys.stderr)

    def start_place() -> None:
        nonlocal streamer, after_stream, placed, stream_bails
        if carry_q is None:
            print("place aborted: nothing carried", file=sys.stderr)
            advance()
            return
        drop_xy = (COUNTER_DROP_X, 0.0)
        stages, err = place_stages(carry_q, drop_xy, carry_place_z, home)
        if err:
            print(f"place failed: {err}", file=sys.stderr)
            advance()
            return
        placed += 1
        streamer = StageStreamer(stages, home, dt, 1.0, integ_cap=0.30)
        stream_bails = set()
        after_stream = {"kind": "place"}
        print(f"placing on counter (#{placed})", file=sys.stderr)

    def advance() -> None:
        nonlocal pending
        if not queue:
            print("subtask plan complete; idling", file=sys.stderr)
            return
        subtask = queue.pop(0)
        op = subtask["op"]
        if op == "goto":
            pending = {
                "op": "goto",
                "location": subtask["location"],
                "target": locations[subtask["location"]],
            }
            send_nav({"location": subtask["location"]})
        elif op == "pick":
            pending = {"op": "pick", "slot": subtask["slot"], "category": subtask["category"]}
            send_nav({"pose": park_pose_for_slot(plano, subtask["slot"])})
        elif op == "place":
            # fix 1: re-park at the per-placement pose, then place at the
            # proven base-frame drop spot
            target = place_park_pose(locations["counter"], placed)
            pending = {"op": "place", "target": target}
            send_nav({"pose": target})
        else:
            print(f"unknown subtask {subtask}", file=sys.stderr)
            advance()

    def settle_target(ctx: dict) -> list[float]:
        if ctx["op"] == "pick":
            return park_pose_for_slot(plano, ctx["slot"])
        return ctx["target"]

    def settle_action(ctx: dict) -> None:
        if ctx["op"] == "pick":
            start_pick(ctx["slot"], ctx["category"])
        elif ctx["op"] == "place":
            start_place()
        else:
            advance()

    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "reset_done":
            clear()
        elif event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            roster = [item.item_id for item in stocked_items(plano, goal)]
        elif event["id"] == "order":
            order = json.loads(event["value"][0].as_py())
            print(f"order read: {order['order']}", file=sys.stderr)
        elif event["id"] == "subtask_plan":
            if queue or streamer is not None:
                continue  # one plan per episode
            raw = json.loads(event["value"][0].as_py())["subtasks"]
            queue, dropped = filter_plan(raw)
            print(
                f"plan received: {len(raw)} subtasks, kept {len(queue)}"
                f" (dropped {dropped} L0 pick quads)",
                file=sys.stderr,
            )
            if not queue:
                print("no feasible subtasks; idling", file=sys.stderr)
                continue
            advance()
        elif event["id"] == "base_pose":
            base_pose = [float(v) for v in event["value"].to_numpy(zero_copy_only=False)[:3]]
            if settling is not None:
                settle_window.append(list(base_pose))
                if len(settle_window) > 10:
                    settle_window.pop(0)
                if len(settle_window) == 10:
                    xs = [p[0] for p in settle_window]
                    ys = [p[1] for p in settle_window]
                    yaws = [p[2] for p in settle_window]
                    still = (
                        max(xs) - min(xs) < 1e-3
                        and max(ys) - min(ys) < 1e-3
                        and max(yaws) - min(yaws) < 5e-3
                    )
                    if still:
                        ctx, settling = settling, None
                        settle_window = []
                        target = settle_target(ctx)
                        pos_err = math.hypot(base_pose[0] - target[0], base_pose[1] - target[1])
                        yaw_err = abs(_wrap(base_pose[2] - target[2]))
                        if (
                            pos_err > nav_params["capture_tol_m"]
                            or yaw_err > nav_params["arrival_yaw_rad"]
                        ):
                            if ctx["reparks"] < 3:
                                print(
                                    f"settled off-target (pos {pos_err:.3f}, yaw {yaw_err:.3f});"
                                    f" re-navigating ({ctx['reparks'] + 1})",
                                    file=sys.stderr,
                                )
                                pending = {**ctx, "reparks": ctx["reparks"] + 1}
                                if ctx["op"] == "goto":
                                    send_nav({"location": ctx["location"]})
                                else:
                                    send_nav({"pose": target})
                            else:
                                print("re-navigation budget exhausted; continuing", file=sys.stderr)
                                settle_action(ctx)
                        else:
                            settle_action(ctx)
        elif event["id"] == "poses":
            latest_poses = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
        elif event["id"] == "nav_result":
            result = json.loads(event["value"][0].as_py())
            if pending is None:
                continue
            done, pending = pending, None
            if result.get("status") != "success":
                retries = done.get("retries", 0)
                if retries < 3:
                    print(f"nav failed ({result}); retry {retries + 1}", file=sys.stderr)
                    pending = {**done, "retries": retries + 1}
                    if done["op"] == "pick":
                        send_nav({"pose": park_pose_for_slot(plano, done["slot"])})
                    elif done["op"] == "place":
                        send_nav({"pose": done["target"]})
                    else:
                        send_nav({"location": done["location"]})
                else:
                    # fix 3: proceed into settle/verify rather than idling
                    # to the episode timeout
                    print(f"nav failed after retries ({result}); settling anyway", file=sys.stderr)
                    settling = {**done, "reparks": done.get("reparks", 0)}
                    settle_window = []
            else:
                settling = {**done, "reparks": done.get("reparks", 0)}
                settle_window = []
        elif event["id"] == "joint_state" and streamer is not None:
            qpos = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            full_cmd, grip_out, logs = streamer.step(qpos)
            metadata = {"env_id": 0}
            if grip_out is not None:
                send("gripper_cmd", pa.array(np.array([grip_out], dtype=np.float32)), metadata)
            if full_cmd is not None:
                send("joint_cmd", pa.array(full_cmd), metadata)
            for line in logs:
                print(line, file=sys.stderr)
                if " bailed at joint " in line:
                    stream_bails.add(line.split()[1])
            if streamer.done:
                ctx, streamer = after_stream, None
                after_stream = None
                if ctx and ctx["kind"] == "pick" and stream_bails & GRASP_CRITICAL:
                    abort_pick()
                elif ctx and ctx["kind"] == "abort":
                    skip_pick_pair()
                else:
                    advance()


if __name__ == "__main__":
    main()
