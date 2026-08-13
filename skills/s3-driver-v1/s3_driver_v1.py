"""s3-driver-v1: agent-authored S3 re-shelving driver (campaign H3-S3, I1).

Plans directly from the episode goal's `misplaced` entries (no
order-reader/task-planner). A swap is executed as the classic 3-move
cycle through a buffer:

  1. pick the wrong occupant out of slot A, buffer it on the COUNTER
     (the proven S1 base-frame (0.50, 0) drop — S3 has no order goal, so
     counter rules and extra_item are inactive; a transient buffer there
     is penalty-free),
  2. pick slot A's own item out of slot B, place it INTO slot A
     (S2-proven recipe: re-park the base in front of the slot, top-down
     drop at the slot center translated into the LIVE base frame),
  3. retrieve the buffered item from the counter, place it into slot B.

New vs the lost s2-driver-v1, whose dominant failure was `misaligned`
(yaw err ~1 rad): the shelf place computes the wrist spin from the
MEASURED in-hand offset — box yaw from the non-privileged `poses` topic
vs gripper yaw from FK at place time — so the box lands at the slot yaw
mod 2pi (both the folded 10-degree criterion AND front_face). Each
placement is verified from `poses` and repaired once (re-pick, re-place
with a freshly measured offset, which also recovers a pi-flipped box).

L0-involving swaps are refused at plan time (v1): both the pick and the
place at a bottom-level slot are outside the proven envelope (S1 I3
finding: the descent jams on the L1 board), and S3 requires BOTH swap
slots to be picked from and placed into, so any L0 slot makes the
episode unwinnable — idling is the safe no-op (no wrong_slot risk).

Pure planning/geometry at module level (CON-12); dora only in main().
"""

from __future__ import annotations

import dataclasses
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
from aisle.verifier.retail import build_retail_cfg
from aisle.verifier.retail import placement_check as verifier_placement_check

PARK_STANDOFF_M = 0.48
# counter buffer standoff. 0.42 (not S1's 0.50): the RE-PICK's retract
# waypoint above ~0.50 exceeds the top-down envelope for tall meds
# (registration eval seed 34: two stacked park drifts put the box at
# ~0.55 and the retract IK-failed). The buffer is a fixed WORLD point
# and the re-pick park is computed FROM the measured box pose, so the
# box sits at 0.42 +/- one capture tol (<=0.495 — offline sweep clean
# for every med at approach 0.06; 0.52 still fails cetirizine)
COUNTER_DROP_X = 0.42
COUNTER_APPROACH_M = 0.06
GRASP_CRITICAL = frozenset({"pregrasp", "advance", "close"})
GRIP_FROM_TOP = 0.035  # GRIP_ENGAGEMENT: TCP this far below the box top
PLACE_HOVER = 0.02  # box bottom hovers this above the surface at release
TRANSFER_LIFT = 0.06  # transfer height above the release TCP
# pos/yaw self-check bands, tightened BELOW the verifier's 0.02 m /
# 10 deg; the other three RS-4 criteria run at the verifier's own bands
# (place_check delegates to the verifier's placement_check, PR #75
# review) so a pass here implies a verifier pass on all five
CHECK_POS_M = 0.016
CHECK_YAW_RAD = 0.14
J7_MIN, J7_MAX = -2.8973, 2.8973


class PickStreamGate:
    """Grasp-critical bail firewall around a pick StageStreamer (PR #54
    review, reused here per PR #75 review): the MOMENT
    pregrasp/advance/close bails, all further stream output is
    suppressed — including the bailed tick's own command. Without it
    the streamer marches on and issues every later close/lift/retract/
    carry command with tracking already declared unsafe, preserving the
    blind-close/neighbour-snag failure mode."""

    def __init__(self, streamer) -> None:
        self.streamer = streamer
        self.bails: set[str] = set()
        self.critical_bail: str | None = None

    @property
    def done(self) -> bool:
        return self.critical_bail is not None or self.streamer.done

    def step(self, qpos):
        if self.critical_bail is not None:
            return None, None, []
        full_cmd, grip_out, logs = self.streamer.step(qpos)
        for line in logs:
            if " bailed at joint " in line:
                stage = line.split()[1]
                self.bails.add(stage)
                if stage in GRASP_CRITICAL:
                    self.critical_bail = stage
                    return None, None, logs
        return full_cmd, grip_out, logs


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def slot_level(slot_id: str) -> int:
    return int(slot_id.split("-L")[1].split("-")[0])


def park_pose_for_slot(plano: dict, slot_id: str) -> list[float]:
    """Store-frame [x, y, yaw] parking PARK_STANDOFF_M in front of the
    slot, facing the unit (s1-driver-v2, unchanged)."""
    world, unit_yaw = slot_world_pose(plano, slot_id)
    return [
        world[0] + PARK_STANDOFF_M * math.cos(unit_yaw),
        world[1] + PARK_STANDOFF_M * math.sin(unit_yaw),
        _wrap(unit_yaw + math.pi),
    ]


def to_base_frame(p_store, base_pose) -> list[float]:
    bx, by, byaw = float(base_pose[0]), float(base_pose[1]), float(base_pose[2])
    dx, dy = float(p_store[0]) - bx, float(p_store[1]) - by
    cos_y, sin_y = math.cos(-byaw), math.sin(-byaw)
    return [dx * cos_y - dy * sin_y, dx * sin_y + dy * cos_y, float(p_store[2])]


def _yaw_quat_xyzw(yaw: float) -> np.ndarray:
    half = yaw / 2
    return np.array([0.0, 0.0, math.sin(half), math.cos(half)], dtype=np.float32)


def flange_yaw(q_arm: np.ndarray) -> float:
    """Yaw of the flange rotation at q (base frame)."""
    rot = fk_flange(np.asarray(q_arm, dtype=np.float64))[1]
    return math.atan2(rot[1, 0], rot[0, 0])


def swap_plan(goal: dict) -> tuple[list[dict], str | None]:
    """The 3-move buffered swap, or (None-plan, reason) when infeasible.

    Entry naming: eb = the entry whose item is BUFFERED (its found_in
    slot is emptied first), ed = the direct entry."""
    entries = goal.get("misplaced", [])
    if len(entries) != 2:
        return [], f"unsupported goal (need 2 misplaced entries, got {len(entries)})"
    for e in entries:
        if slot_level(e["found_in"]) == 0 or slot_level(e["belongs_in"]) == 0:
            return [], f"L0 slot in swap ({e['found_in']}<->{e['belongs_in']}); infeasible in v1"
    # eb: buffered item = the one found in the slot we empty first.
    # Emptying entries[0].belongs_in first means buffering the item found
    # THERE, which is entries[1].item (the swap partner).
    ed, eb = entries[0], entries[1]
    slot_a, slot_b = ed["belongs_in"], eb["belongs_in"]
    tasks = [
        # move 1: clear slot A, buffer its wrong occupant (eb.item) on the counter
        {"op": "goto_park", "slot": slot_a},
        {"op": "pick", "item": eb["item"], "verify_min_lift": 0.05},
        {"op": "goto_loc", "location": "counter"},
        {"op": "place_counter", "item": eb["item"]},
        # move 2: slot A's own item comes home from slot B
        {"op": "goto_park", "slot": slot_b},
        {"op": "pick", "item": ed["item"], "verify_min_lift": 0.05},
        {"op": "goto_park", "slot": slot_a},
        {"op": "place_slot", "item": ed["item"], "slot": slot_a},
        # move 3: the buffered item comes home to slot B (the park is
        # computed from the MEASURED box pose at dispatch time so the
        # re-pick sees the box at the proven base-frame standoff)
        {"op": "goto_counter_pick", "item": eb["item"]},
        {"op": "pick", "item": eb["item"], "verify_min_lift": 0.05},
        {"op": "goto_park", "slot": slot_b},
        {"op": "place_slot", "item": eb["item"], "slot": slot_b},
    ]
    return tasks, None


def pick_stages(
    item_pos_base, item_yaw_base: float, size_xyz, home: np.ndarray, approach_m: float = 0.15
) -> tuple[list[Stage] | None, np.ndarray | None, float, str | None]:
    """Top-down pick (s1-driver-v2 lineage), parameterized approach so
    counter-height picks keep their staging within the IK envelope.
    Returns (stages, q_carry, grasp_gripper_yaw_base, err)."""
    folded_yaw = ((item_yaw_base + math.pi / 2) % math.pi) - math.pi / 2
    target_pose = np.concatenate(
        [np.asarray(item_pos_base, dtype=np.float32), _yaw_quat_xyzw(folded_yaw)]
    )
    grasp, _, _ = plan_grasp(target_pose, size_xyz, front=False, tray_top_z=0.55)
    grasp_pos = grasp[:3].astype(np.float64)
    grasp_rot = quat_to_rotation(grasp[3:7])
    grasp_yaw = math.atan2(grasp_rot[1, 0], grasp_rot[0, 0])
    home_arm = np.asarray(home, dtype=np.float32)[:7]
    pre_pos = grasp_pos - grasp_rot[:, 2] * approach_m
    up = np.array([0.0, 0.0, 0.015])

    home_tcp = fk_tcp(home_arm)
    staging_z = max(STAGING_Z, float(pre_pos[2]))
    rise_pos = np.array([home_tcp[0], home_tcp[1], staging_z])
    staging_pos = np.array([pre_pos[0], pre_pos[1], staging_z])
    q_rise = ik_solve(rise_pos, grasp_rot, home_arm)
    if q_rise is None:
        return None, None, grasp_yaw, "IK failed: rise"
    staging_path = ik_continuation(rise_pos, staging_pos, grasp_rot, q_rise)
    if staging_path is None:
        return None, None, grasp_yaw, "IK failed: staging"
    pregrasp_path = ik_continuation(staging_pos, pre_pos, grasp_rot, staging_path[-1])
    if pregrasp_path is None:
        return None, None, grasp_yaw, "IK failed: pregrasp"
    advance_path = ik_continuation(pre_pos, grasp_pos, grasp_rot, pregrasp_path[-1])
    if advance_path is None:
        return None, None, grasp_yaw, "IK failed: advance"
    lift_path = ik_continuation(grasp_pos, grasp_pos + up, grasp_rot, advance_path[-1])
    if lift_path is None:
        return None, None, grasp_yaw, "IK failed: lift"
    retract_path = ik_continuation(grasp_pos + up, pre_pos + up, grasp_rot, lift_path[-1])
    if retract_path is None:
        return None, None, grasp_yaw, "IK failed: retract"
    carry_pos = np.array([home_tcp[0], home_tcp[1], staging_z])
    carry_path = ik_continuation(pre_pos + up, carry_pos, grasp_rot, retract_path[-1])
    if carry_path is None:
        return None, None, grasp_yaw, "IK failed: carry"
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
    return stages, carry_path[-1], grasp_yaw, None


def place_stages(
    q_start: np.ndarray,
    drop_xy,
    place_tcp_z: float,
    home: np.ndarray,
    target_yaw_base: float | None = None,
) -> tuple[list[Stage] | None, str | None]:
    """Top-down place (s1-driver-v2 lineage). With target_yaw_base None,
    the wrist unwinds to the nearest in-limit 0/pi spin (counter buffer:
    yaw is free). With a target, the wrist spins so the GRIPPER lands at
    that base-frame yaw exactly (mod 2pi) — the caller derives it from
    the measured in-hand offset so the BOX lands at the slot yaw."""
    home_arm = np.asarray(home, dtype=np.float32)[:7]
    q_start = np.asarray(q_start, dtype=np.float32)[:7]
    start_tcp = fk_tcp(q_start)
    yaw_cur = flange_yaw(q_start)
    q_unwind = q_start.copy()
    best = None
    if target_yaw_base is None:
        candidates = (0.0, math.pi, -math.pi)
    else:
        candidates = (target_yaw_base, target_yaw_base + 2 * math.pi, target_yaw_base - 2 * math.pi)
    for target in candidates:
        j7 = q_start[6] - (yaw_cur - target)
        if J7_MIN <= j7 <= J7_MAX:
            if best is None or abs(j7 - q_start[6]) < abs(best[0] - q_start[6]):
                best = (j7, target)
    if best is None:
        return None, "unwind: no in-limit wrist spin"
    q_unwind[6] = best[0]
    place_rot = topdown_rotation(best[1])
    transfer_z = place_tcp_z + TRANSFER_LIFT
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


def place_check(box_pos, box_yaw: float, slot_id: str, plano: dict, half, cfg) -> str | None:
    """Self-check a placement from `poses` against ALL FIVE RS-4
    criteria by delegating to the verifier's own placement_check
    (PR #75 review: the old XY+yaw-only check returned None for a box
    10 cm above its slot and ignored overhang/alignment, turning
    repairable placements into timeouts). pos/yaw run tightened bands;
    the other three run the verifier's own, so a pass here implies a
    verifier pass. Returns the first failing criterion, None iff good."""
    check_cfg = dataclasses.replace(
        cfg,
        pos_tol_m=min(cfg.pos_tol_m, CHECK_POS_M),
        yaw_tol_deg=min(cfg.yaw_tol_deg, math.degrees(CHECK_YAW_RAD)),
    )
    result = verifier_placement_check(
        np.asarray(box_pos, dtype=np.float32), box_yaw, half, slot_id, plano, check_cfg
    )
    for criterion in ("pos", "yaw", "front_face", "overhang", "alignment"):
        if not result[criterion]:
            return criterion
    return None


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
    from aisle.mobility.nav import load_locations, load_nav_params, nav_result_is_current

    locations = load_locations()
    nav_params = load_nav_params("mobile")
    counter_top = plano["store"]["counter_pos"][2] + plano["store"]["counter_size"][2] / 2
    # the buffer's fixed WORLD point: COUNTER_DROP_X ahead of the nominal
    # counter park along its facing
    _cloc = locations["counter"]
    buffer_world = [
        _cloc[0] + COUNTER_DROP_X * math.cos(_cloc[2]),
        _cloc[1] + COUNTER_DROP_X * math.sin(_cloc[2]),
        counter_top,  # z unused by the drop but to_base_frame needs 3d
    ]
    dt = 0.01

    node = Node()
    send = make_sender(node)

    goal = None
    retail_cfg = None
    roster: list[str] = []
    queue: list[dict] = []
    pending: dict | None = None
    settling: dict | None = None
    settle_window: list[list[float]] = []
    streamer = None
    after_stream: dict | None = None
    stream_bails: set[str] = set()
    base_pose = [0.0, 0.0, 0.0]
    latest_poses: np.ndarray | None = None
    carry_q: np.ndarray | None = None
    carried: dict | None = None  # {item, size, retried}
    place_attempts = 0
    nav_seq = 0
    # the episode this driver is in: reset_done's TC-2 seq, stamped onto
    # every nav_goal so waypoint-nav can refuse a goal that crossed the
    # boundary in flight (issue #179 review). NOT reset by clear() --
    # like nav_seq it is process-scoped, and its value comes from the
    # boundary message itself, so the two sides cannot drift.
    episode_epoch: int | None = None

    def clear() -> None:
        nonlocal goal, roster, queue, pending, streamer, after_stream, place_attempts
        nonlocal carry_q, carried, latest_poses, settling, settle_window, stream_bails
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
        carried = None
        place_attempts = 0
        latest_poses = None

    def send_nav(nav_goal: dict) -> None:
        # stamps the issued goal_id onto `pending` (issue #179) so the
        # nav_result handler can tell OUR leg's reply from a stale one. Done
        # here rather than at each `pending = {...}` site because a retry
        # reuses `{**done, ...}`, which would otherwise carry the PREVIOUS
        # leg's id and reject its own reply.
        nonlocal nav_seq, pending
        nav_seq += 1
        goal_id = f"nav-{nav_seq:03d}"
        if pending is not None:
            pending = {**pending, "goal_id": goal_id}
        send(
            "nav_goal",
            pa.array([json.dumps(nav_goal)]),
            {"goal_id": goal_id, "episode_epoch": episode_epoch},
        )

    def item_pose(item_id: str) -> tuple[list[float], float] | None:
        """(world pos3, world yaw) of one item from the poses topic."""
        if latest_poses is None or item_id not in roster:
            return None
        idx = roster.index(item_id)
        block = latest_poses[idx * 7 : idx * 7 + 7]
        x, y, z, w = (float(v) for v in block[3:7])
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        return [float(v) for v in block[:3]], yaw

    def start_pick(task: dict) -> None:
        nonlocal streamer, carry_q, carried, after_stream, stream_bails
        item_id = task["item"]
        pose = item_pose(item_id)
        if pose is None:
            print(f"pick aborted: no pose for {item_id}", file=sys.stderr)
            fail_item(item_id)
            return
        (pos_w, yaw_w) = pose
        # category from the item id's home slot (ids are "slot#k")
        category = plano["slots"][item_id.split("#")[0]]["category"]
        size = meds[category]["size"]
        pos_base = to_base_frame(pos_w, base_pose)
        yaw_base = _wrap(yaw_w - base_pose[2])
        # counter-height picks: shorter approach keeps the retract
        # waypoint inside the top-down envelope (offline sweep)
        approach = COUNTER_APPROACH_M if pos_w[2] > 0.5 else 0.15
        stages, q_carry, _, err = pick_stages(pos_base, yaw_base, size, home, approach)
        if err:
            print(f"pick {item_id} failed: {err}", file=sys.stderr)
            fail_item(item_id)
            return
        # pick streams run behind the bail firewall (PR #54/#75 reviews):
        # a grasp-critical bail must stop the stream THAT tick
        streamer = PickStreamGate(StageStreamer(stages, home, dt, 1.0, integ_cap=0.30))
        stream_bails = set()
        carry_q = q_carry
        carried = {
            "item": item_id,
            "size": size,
            "spawn_z": pos_w[2],
            "retried": task.get("retried", False),
        }
        after_stream = {"kind": "pick", "task": task}
        print(
            f"picking {item_id} at world ({pos_w[0]:.2f},{pos_w[1]:.2f},{pos_w[2]:.2f})",
            file=sys.stderr,
        )

    def fail_item(item_id: str) -> None:
        """Drop every remaining task touching this item; keep the rest
        (best effort — the episode may already be unwinnable, but later
        tasks exercise real behavior and never make the verdict worse)."""
        nonlocal queue, carry_q, carried
        before = len(queue)
        pruned: list[dict] = []
        i = 0
        while i < len(queue):
            t = queue[i]
            nxt = queue[i + 1] if i + 1 < len(queue) else None
            if (
                t["op"] in ("goto_park", "goto_loc")
                and nxt is not None
                and nxt.get("item") == item_id
            ):
                i += 1  # the goto that led into the dropped pick/place
                continue
            if t.get("item") == item_id:
                i += 1
                continue
            pruned.append(t)
            i += 1
        queue = pruned
        carry_q = None
        carried = None
        print(f"item {item_id} failed; dropped {before - len(queue)} tasks", file=sys.stderr)
        advance()

    def start_place(task: dict) -> None:
        nonlocal streamer, after_stream, stream_bails, place_attempts
        if carry_q is None or carried is None:
            print("place aborted: nothing carried", file=sys.stderr)
            advance()
            return
        size = carried["size"]
        if task["op"] == "place_counter":
            # fixed WORLD buffer point (registration eval seed 34: a
            # base-frame drop compounds the two counter parks' drifts)
            drop = to_base_frame(buffer_world, base_pose)
            drop_xy = (drop[0], drop[1])
            tcp_z = counter_top + (float(size[2]) - GRIP_FROM_TOP) + PLACE_HOVER
            stages, err = place_stages(carry_q, drop_xy, tcp_z, home, None)
            label = "counter"
        else:
            slot_id = task["slot"]
            world, slot_yaw = slot_world_pose(plano, slot_id)
            drop = to_base_frame(world, base_pose)
            # measured in-hand offset: spin the wrist so the BOX lands at
            # the slot yaw (fixes s2-driver-v1's misaligned class). The
            # task's yaw_bias (set by a failed self-check) aims OFF by
            # the previously measured landing error so a DETERMINISTIC
            # twist imparted by the place motion cancels (dev seed 19:
            # cetirizine lands -0.18 rad twice, identically, just over
            # the 10-degree criterion)
            pose = item_pose(carried["item"])
            gripper_yaw_now = flange_yaw(carry_q)
            bias = float(task.get("yaw_bias", 0.0))
            if pose is None:
                target_yaw = _wrap(slot_yaw - base_pose[2] + bias)  # assume no offset
                tcp_z = world[2] + (float(size[2]) - GRIP_FROM_TOP) + PLACE_HOVER
            else:
                box_yaw_w = pose[1]
                target_yaw = _wrap(slot_yaw - box_yaw_w + gripper_yaw_now + bias)
                # measured grip depth: release the box bottom PLACE_HOVER
                # above the board even when the close sagged deeper than
                # GRIP_FROM_TOP (a gripped box dragged on the board twists)
                drop_h = float(fk_tcp(np.asarray(carry_q, dtype=np.float32)[:7])[2]) - (
                    float(pose[0][2]) - float(size[2]) / 2
                )
                drop_h = min(max(drop_h, float(size[2]) - 0.06), float(size[2]) + 0.01)
                tcp_z = world[2] + PLACE_HOVER + drop_h
            stages, err = place_stages(carry_q, (drop[0], drop[1]), tcp_z, home, target_yaw)
            label = f"{slot_id} (gripper yaw {target_yaw:.2f}, bias {bias:.2f})"
        if err:
            print(f"place {label} failed: {err}", file=sys.stderr)
            fail_item(carried["item"])
            return
        streamer = StageStreamer(stages, home, dt, 1.0, integ_cap=0.30)
        stream_bails = set()
        after_stream = {"kind": task["op"], "task": task}
        print(f"placing {carried['item']} at {label}", file=sys.stderr)

    def verify_pick(task: dict) -> None:
        """After the pick stream: the box must have LEFT its spawn height
        or the grasp is untrusted (release at carry and retry once)."""
        nonlocal carry_q, carried
        item_id = task["item"]
        pose = item_pose(item_id)
        lifted = pose is not None and pose[0][2] > carried["spawn_z"] + task.get(
            "verify_min_lift", 0.05
        )
        bailed = bool(stream_bails & GRASP_CRITICAL)
        if lifted and not bailed:
            print(f"grasp verified: {item_id}", file=sys.stderr)
            advance()
            return
        print(
            f"grasp untrusted for {item_id} (lifted={lifted}, bails={sorted(stream_bails)})",
            file=sys.stderr,
        )
        abort_release(task)

    def abort_release(task: dict) -> None:
        """Open at the carry tuck (never over a slot), then retry the pick
        once or fail the item."""
        nonlocal streamer, after_stream, stream_bails
        home_arm = np.asarray(home, dtype=np.float32)[:7]
        release_q = carry_q if carry_q is not None else home_arm
        stages = [
            Stage("abort_release", (np.asarray(release_q, dtype=np.float32),), 0.0, 1.2),
            Stage("home", (home_arm,), 0.0, 0.0),
        ]
        streamer = StageStreamer(stages, home, dt, 1.0, integ_cap=0.30)
        stream_bails = set()
        after_stream = {"kind": "abort", "task": task}

    def verify_place(task: dict) -> None:
        """After a slot place: self-check from poses; one repair."""
        nonlocal carry_q, carried, place_attempts, queue
        item_id = task["item"]
        slot_id = task["slot"]
        pose = item_pose(item_id)
        half = tuple(float(s) / 2 for s in carried["size"]) if carried else (0.02, 0.02, 0.05)
        problem = (
            "no pose"
            if pose is None
            else place_check(pose[0], pose[1], slot_id, plano, half, retail_cfg)
        )
        if problem is None:
            print(f"placement verified: {item_id} -> {slot_id}", file=sys.stderr)
            carry_q = None
            carried = None
            place_attempts = 0
            advance()
            return
        if place_attempts < 2:
            place_attempts += 1
            # aim the re-place OFF by the measured landing error: a
            # deterministic twist from the place dynamics cancels (I3)
            bias = 0.0
            if pose is not None:
                _, slot_yaw = slot_world_pose(plano, slot_id)
                err = _wrap(pose[1] - slot_yaw)
                folded = ((err + math.pi / 2) % math.pi) - math.pi / 2
                bias = max(-0.35, min(0.35, -folded))
            print(
                f"placement check failed ({problem}); repairing {item_id} -> {slot_id}"
                f" (attempt {place_attempts + 1}, yaw_bias {bias:.2f})",
                file=sys.stderr,
            )
            carry_q = None
            carried = None
            queue = [
                {"op": "pick", "item": item_id, "verify_min_lift": 0.04},
                {"op": "place_slot", "item": item_id, "slot": slot_id, "yaw_bias": bias},
            ] + queue
            advance()
            return
        print(f"placement FAILED after repair ({problem}): {item_id} -> {slot_id}", file=sys.stderr)
        carry_q = None
        carried = None
        place_attempts = 0
        advance()

    def advance() -> None:
        nonlocal pending
        if not queue:
            print("task plan complete; idling", file=sys.stderr)
            return
        task = queue.pop(0)
        op = task["op"]
        if op == "goto_park":
            target = park_pose_for_slot(plano, task["slot"])
            pending = {"task": task, "target": target}
            send_nav({"pose": target})
        elif op == "goto_loc":
            target = locations[task["location"]]
            pending = {"task": task, "target": target}
            send_nav({"location": task["location"]})
        elif op == "goto_counter_pick":
            # park so the MEASURED box sits at the proven standoff
            pose = item_pose(task["item"])
            yaw = locations["counter"][2]
            if pose is None:
                target = locations["counter"]
            else:
                target = [
                    pose[0][0] - COUNTER_DROP_X * math.cos(yaw),
                    pose[0][1] - COUNTER_DROP_X * math.sin(yaw),
                    yaw,
                ]
            pending = {"task": task, "target": target}
            send_nav({"pose": target})
        elif op == "pick":
            start_pick(task)
        elif op in ("place_counter", "place_slot"):
            start_place(task)
        else:
            print(f"unknown task {task}", file=sys.stderr)
            advance()

    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "reset_done":
            episode_epoch = (event.get("metadata") or {}).get("seq")
            clear()
        elif event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            retail_cfg = build_retail_cfg(plano, goal)
            roster = [item.item_id for item in stocked_items(plano, goal)]
            if queue or streamer is not None:
                continue  # one plan per episode
            tasks, reason = swap_plan(goal)
            if reason:
                print(f"plan refused: {reason}; idling", file=sys.stderr)
                continue
            queue = tasks
            print(
                f"plan: swap {goal['misplaced'][0]['belongs_in']} <-> "
                f"{goal['misplaced'][1]['belongs_in']} ({len(queue)} tasks)",
                file=sys.stderr,
            )
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
                        target = ctx["target"]
                        pos_err = math.hypot(base_pose[0] - target[0], base_pose[1] - target[1])
                        yaw_err = abs(_wrap(base_pose[2] - target[2]))
                        if (
                            pos_err > nav_params["capture_tol_m"]
                            or yaw_err > nav_params["arrival_yaw_rad"]
                        ) and ctx.get("reparks", 0) < 3:
                            print(
                                f"settled off-target (pos {pos_err:.3f}, yaw {yaw_err:.3f});"
                                f" re-parking ({ctx.get('reparks', 0) + 1})",
                                file=sys.stderr,
                            )
                            pending = {**ctx, "reparks": ctx.get("reparks", 0) + 1}
                            if ctx["task"]["op"] == "goto_loc":
                                send_nav({"location": ctx["task"]["location"]})
                            else:
                                send_nav({"pose": target})
                        else:
                            advance()
        elif event["id"] == "poses":
            latest_poses = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
        elif event["id"] == "nav_result":
            result = json.loads(event["value"][0].as_py())
            if pending is None:
                continue
            reply_id = (event.get("metadata") or {}).get("goal_id")
            if not nav_result_is_current(pending.get("goal_id"), reply_id):
                # issue #179: a reply from a leg we are NOT waiting on --
                # above all one carried over from a previous episode -- must
                # never complete the live subtask
                print(
                    f"stale nav_result ignored: goal_id={reply_id!r} "
                    f"(waiting on {pending.get('goal_id')!r}); {result}",
                    file=sys.stderr,
                )
                continue
            done, pending = pending, None
            if result.get("status") != "success":
                retries = done.get("retries", 0)
                if retries < 3:
                    print(f"nav failed ({result}); retry {retries + 1}", file=sys.stderr)
                    pending = {**done, "retries": retries + 1}
                    if done["task"]["op"] == "goto_loc":
                        send_nav({"location": done["task"]["location"]})
                    else:
                        send_nav({"pose": done["target"]})
                else:
                    print(f"nav failed after retries ({result}); settling anyway", file=sys.stderr)
                    settling = done
                    settle_window = []
            else:
                settling = done
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
            if isinstance(streamer, PickStreamGate) and streamer.critical_bail:
                # PR #54/#75 reviews: abort NOW — the gate already
                # suppressed this tick's command, and waiting for the
                # stream to run dry would issue every later close/lift/
                # retract/carry command with tracking declared unsafe
                ctx, streamer = after_stream, None
                after_stream = None
                abort_release(ctx["task"])
            elif streamer.done:
                ctx, streamer = after_stream, None
                after_stream = None
                if ctx is None:
                    advance()
                elif ctx["kind"] == "pick":
                    verify_pick(ctx["task"])
                elif ctx["kind"] == "abort":
                    task = ctx["task"]
                    if task.get("retried"):
                        fail_item(task["item"])
                    else:
                        start_pick({**task, "retried": True})
                elif ctx["kind"] == "place_slot":
                    verify_place(ctx["task"])
                else:  # place_counter: confirm the buffer landed, move on
                    task = ctx["task"]
                    pose = item_pose(task["item"])
                    carry_q = None
                    carried = None
                    if pose is None or pose[0][2] < counter_top - 0.02:
                        print(f"buffer drop LOST {task['item']}", file=sys.stderr)
                        fail_item(task["item"])
                    else:
                        print(f"buffered {task['item']} on counter", file=sys.stderr)
                        advance()


if __name__ == "__main__":
    main()
