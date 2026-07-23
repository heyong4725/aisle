"""Retail verifier (SPEC 200 RS-4..9, ADR-16).

ONE parameterized judge for S1/S2/S3 (RS-5): requirements are derived from
GOAL PARAMETERS — an `order` enables the counter rules (RS-7), `restock`
entries require categories in slots (RS-8), `misplaced` entries require
the named items home (RS-9) — no scenario forks in the verdict logic.
Pure functions in the VER-1 discipline: no dora, no sim; every threshold
from verifier/placement.toml (RS-4).
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aisle.scenes.pharmacy import load_meds
from aisle.scenes.store import slot_world_pose, spawn_pose, spec_for, stocked_items

_VERIFIER_DIR = Path(__file__).parent

# RS-4: the retail classes join the existing VER-3 taxonomy
RETAIL_FAILURE_CLASSES = (
    "misplaced",
    "misaligned",
    "overhang",
    "wrong_slot",
    "missing_item",
    "extra_item",
    "timeout",
)

_CRITERION_CLASS = {
    "pos": "misplaced",
    "yaw": "misaligned",
    "front_face": "misaligned",
    "overhang": "overhang",
    "alignment": "misaligned",
}


def load_placement() -> dict:
    with open(_VERIFIER_DIR / "placement.toml", "rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class RetailCfg:
    """Geometry + thresholds the judge needs beyond the planogram/goal:
    the item roster in ORACLE ORDER (stock order, ADR-15) and the
    placement.toml thresholds."""

    item_ids: tuple[str, ...]
    item_categories: tuple[str, ...]
    item_specs: tuple[str, ...]  # true disambiguator per item (RS-7 matching)
    half_extents: tuple[tuple[float, float, float], ...]
    home_poses: tuple[tuple[float, float, float, float], ...]  # spawn (x,y,z,yaw)
    pos_tol_m: float
    yaw_tol_deg: float
    overhang_tol_m: float
    alignment_tol_m: float
    resting_tol_m: float
    slot_occupancy_radius_m: float
    counter_margin_m: float
    counter_resting_tol_m: float
    timeout_s: float


def build_retail_cfg(plano: dict, episode_goal: dict, placement: dict | None = None) -> RetailCfg:
    """The thresholds/roster wiring in ONE place (mirrors the desk judge's
    threshold_kwargs) so tests build cfgs exactly as production does."""
    placement = placement or load_placement()
    meds = load_meds()
    stock = stocked_items(plano, episode_goal)
    p, c, e = placement["placement"], placement["counter"], placement["episode"]
    return RetailCfg(
        item_ids=tuple(item.item_id for item in stock),
        item_categories=tuple(item.category for item in stock),
        item_specs=tuple(spec_for(item.category, meds) for item in stock),
        half_extents=tuple(tuple(s / 2 for s in meds[item.category]["size"]) for item in stock),
        home_poses=tuple(spawn_pose(plano, item, meds) for item in stock),
        pos_tol_m=p["pos_tol_m"],
        yaw_tol_deg=p["yaw_tol_deg"],
        overhang_tol_m=p["overhang_tol_m"],
        alignment_tol_m=p["alignment_tol_m"],
        resting_tol_m=p["resting_tol_m"],
        slot_occupancy_radius_m=p["slot_occupancy_radius_m"],
        counter_margin_m=c["margin_m"],
        counter_resting_tol_m=c["resting_tol_m"],
        timeout_s=e["timeout_s"],
    )


def _item_pose(state: np.ndarray, idx: int) -> tuple[np.ndarray, float]:
    """(pos3, yaw) of one item from oracle_state (TC-1 x,y,z,w quats)."""
    block = np.asarray(state, dtype=np.float32).reshape(-1)[idx * 7 : idx * 7 + 7]
    x, y, z, w = (float(v) for v in block[3:7])
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return block[:3], yaw


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def placement_check(
    pos: np.ndarray, yaw: float, half, slot_id: str, plano: dict, cfg: RetailCfg
) -> dict:
    """RS-4: the five per-criterion pass/fail booleans for one item against
    one slot (ADR-16 definitions)."""
    slot = plano["slots"][slot_id]
    unit = plano["units"][slot_id.split("-")[0]]
    world, slot_yaw = slot_world_pose(plano, slot_id)
    tx, ty = slot["template_pose"][:2]
    depth = plano["store"]["unit_geometry"]["depth"]

    # item position in the UNIT frame
    cos_y, sin_y = math.cos(unit["yaw"]), math.sin(unit["yaw"])
    dx, dy = float(pos[0]) - unit["pos"][0], float(pos[1]) - unit["pos"][1]
    ux = dx * cos_y + dy * sin_y
    uy = -dx * sin_y + dy * cos_y

    err = _wrap(yaw - slot_yaw)
    # long-axis alignment: yaw error folded mod 180 degrees (ADR-16)
    axis_err = ((err + math.pi / 2) % math.pi) - math.pi / 2
    # world extent along the facing axis for a yaw-rotated box
    facing_extent = half[0] * abs(math.cos(err)) + half[1] * abs(math.sin(err))
    front_edge = ux + facing_extent
    template_front = tx + half[0]
    # PR #19 review: pos is a 3D error — vertical offset from the resting
    # template height (board surface + half height) counts too
    dz = float(pos[2]) - (world[2] + half[2])

    return {
        "slot": slot_id,
        "pos": math.hypot(math.hypot(ux - tx, uy - ty), dz) <= cfg.pos_tol_m,
        "yaw": abs(math.degrees(axis_err)) <= cfg.yaw_tol_deg,
        "front_face": math.cos(err) > 0.0,
        "overhang": front_edge <= depth / 2 + cfg.overhang_tol_m,
        "alignment": abs(front_edge - template_front) <= cfg.alignment_tol_m,
    }


def _occupies_slot(pos: np.ndarray, half, slot_id: str, plano: dict, cfg: RetailCfg) -> bool:
    """ADR-16: within the occupancy radius of the slot center AND resting
    on the board."""
    world, _ = slot_world_pose(plano, slot_id)
    if math.hypot(float(pos[0]) - world[0], float(pos[1]) - world[1]) > cfg.slot_occupancy_radius_m:
        return False
    bottom = float(pos[2]) - half[2]
    return abs(bottom - world[2]) <= cfg.resting_tol_m


def _on_counter(pos: np.ndarray, half, plano: dict, cfg: RetailCfg) -> bool:
    """RS-7: within the counter footprint (+margin) and resting on its top."""
    store = plano["store"]
    cx, cy, cz = store["counter_pos"]
    sx, sy, sz = store["counter_size"]
    if abs(float(pos[0]) - cx) > sx / 2 + cfg.counter_margin_m:
        return False
    if abs(float(pos[1]) - cy) > sy / 2 + cfg.counter_margin_m:
        return False
    top = cz + sz / 2
    bottom = float(pos[2]) - half[2]
    return top - cfg.counter_margin_m <= bottom <= top + cfg.counter_resting_tol_m


def _required_slots(plano: dict, episode_goal: dict) -> dict[str, dict]:
    """Goal parameters -> slot requirements (RS-5): slot -> {category,
    item (specific id or None)}. restock requires a category; misplaced
    requires the NAMED item home, and involves both slots of the swap so
    RS-9's 'origin not newly wrong' falls out of the same rule."""
    required: dict[str, dict] = {}
    for entry in episode_goal.get("restock", []):
        required[entry["slot"]] = {"category": entry["category"], "item": None}
    for entry in episode_goal.get("misplaced", []):
        slot = entry["belongs_in"]
        required[slot] = {"category": plano["slots"][slot]["category"], "item": entry["item"]}
    return required


def judge_retail(
    oracle_state: np.ndarray, plano: dict, episode_goal: dict, t: float, cfg: RetailCfg
) -> dict:
    """RS-5: the pure per-sample retail verdict.

    Returns {"status": "success"|"fail"|"ongoing", "penalties": [class...],
    "placement_scores": [per-criterion dicts]}. RS-7's safety asymmetry:
    a non-ordered product on the counter fails IMMEDIATELY; everything
    else accumulates penalties and fails only at the timeout (ADR-16)."""
    state = np.asarray(oracle_state, dtype=np.float32).reshape(-1)
    poses = [_item_pose(state, i) for i in range(len(cfg.item_ids))]

    penalties: list[str] = []
    placement_scores: list[dict] = []

    # --- counter rules (active iff the goal carries an order, RS-7) ---
    satisfied = True
    if "order" in episode_goal:
        order = episode_goal["order"]
        named_products = {line["product"] for line in order}
        counter_idx = [
            idx
            for idx, (pos, _) in enumerate(poses)
            if _on_counter(pos, cfg.half_extents[idx], plano, cfg)
        ]
        for idx in counter_idx:
            if cfg.item_categories[idx] not in named_products:
                # immediate failure, at ANY time (RS-7 safety asymmetry).
                # Keys on the PRODUCT name: an ordered product under an
                # invalid line spec is unfulfillable, not an ambush.
                return {"status": "fail", "penalties": ["extra_item"], "placement_scores": []}
        # PR #19 review: a line is satisfied only by items matching BOTH its
        # product AND its spec disambiguator — an invalid spec can never be
        # satisfied, so the order stays incomplete (RS-7's full triple)
        satisfied = all(
            sum(
                1
                for idx in counter_idx
                if cfg.item_categories[idx] == line["product"]
                and cfg.item_specs[idx] == line["spec"]
            )
            == line["qty"]
            for line in order
        )

    # --- slot rules (restock / misplaced goals; RS-8, RS-9) ---
    for slot_id, req in _required_slots(plano, episode_goal).items():
        occupants = [
            idx
            for idx, (pos, _) in enumerate(poses)
            if _occupies_slot(pos, cfg.half_extents[idx], slot_id, plano, cfg)
        ]
        wrong = [idx for idx in occupants if cfg.item_categories[idx] != req["category"]]
        if wrong:
            penalties.append("wrong_slot")
            satisfied = False
        matches = [
            idx
            for idx in occupants
            if cfg.item_categories[idx] == req["category"]
            and (req["item"] is None or cfg.item_ids[idx] == req["item"])
        ]
        if not matches:
            penalties.append("missing_item")
            satisfied = False
            continue
        idx = matches[0]
        pos, yaw = poses[idx]
        score = {"item": cfg.item_ids[idx]}
        score.update(placement_check(pos, yaw, cfg.half_extents[idx], slot_id, plano, cfg))
        placement_scores.append(score)
        for criterion, ok in score.items():
            if criterion in _CRITERION_CLASS and not ok:
                if _CRITERION_CLASS[criterion] not in penalties:
                    penalties.append(_CRITERION_CLASS[criterion])
                satisfied = False

    # --- verdict (ADR-16: the deadline gates success) ---
    if t >= cfg.timeout_s:
        if "timeout" not in penalties:
            penalties.append("timeout")
        return {"status": "fail", "penalties": penalties, "placement_scores": placement_scores}
    if satisfied:
        return {"status": "success", "penalties": penalties, "placement_scores": placement_scores}
    return {"status": "ongoing", "penalties": penalties, "placement_scores": placement_scores}


def score_episode(verdict: dict, t: float) -> dict:
    """RS-6: the per-episode scoring record."""
    return {
        "success": verdict["status"] == "success",
        "t_end": t,
        "penalties": list(verdict["penalties"]),
        "placement_scores": list(verdict["placement_scores"]),
    }
