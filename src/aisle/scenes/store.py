"""Store scene for the retail suite (SPEC 200 RS-1..3, ADR-15).

The scene is GENERATED from `planogram.toml` — the single source of truth
mapping every shelf slot to {category, template_pose, capacity, shelf_zone}
plus the store geometry (units, counter, bin). Episode generators perturb
the stock per seed (S2 de-stocks, S3 swaps); `build_store` spawns exactly
the resulting stock. Pure logic (planogram, transforms, generators, stock)
lives at module level, sim-free and unit-tested (CON-12); genesis is
imported only inside `build_store` (via pharmacy's `_ensure_genesis`).

Determinism (CON-5): generators are pure functions of (seed, scenario);
the scene is a pure function of (planogram, episode) — same seed, same
store.
"""

from __future__ import annotations

import math
import random
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aisle.scenes.pharmacy import (
    FRANKA_MJCF,
    _ensure_genesis,
    load_meds,
    load_physics,
)

_SCENES_DIR = Path(__file__).parent

SCENARIOS = ("S1", "S2", "S3")

# v0 units sit at quarter-turn yaws (ADR-15): entities spawn with the REAL
# composed rotation (yaw_quat_wxyz), but the pure aisle-width footprint
# math (test_store_layout_navigable) assumes axis-aligned AABBs
_QUARTER_YAWS = (0.0, math.pi / 2, -math.pi / 2, math.pi)


def load_planogram() -> dict:
    """RS-1: the planogram — slots, units, and store geometry."""
    with open(_SCENES_DIR / "planogram.toml", "rb") as f:
        plano = tomllib.load(f)
    for unit_id, unit in plano["units"].items():
        if not any(abs(unit["yaw"] - y) < 1e-3 for y in _QUARTER_YAWS):
            raise ValueError(
                f"unit {unit_id!r} yaw {unit['yaw']} is not a quarter turn; "
                "v0 units are axis-aligned (ADR-15)"
            )
    for slot_id, slot in plano["slots"].items():
        quat = slot["template_pose"][3:]
        if any(abs(q) > 1e-9 for q in quat[:3]) or abs(quat[3] - 1.0) > 1e-9:
            raise ValueError(
                f"slot {slot_id!r} template quat {quat} must be identity in v0 "
                "(item yaw = unit yaw, ADR-15)"
            )
    return plano


def slot_world_pose(plano: dict, slot_id: str) -> tuple[list[float], float]:
    """ADR-15: (world [x, y, z], yaw) of a slot's template — unit
    yaw/translation composed with the unit-frame template pose. z is the
    board surface (item base)."""
    slot = plano["slots"][slot_id]
    unit = plano["units"][slot_id.split("-")[0]]
    tx, ty, tz = slot["template_pose"][:3]
    yaw = unit["yaw"]
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return (
        [
            unit["pos"][0] + tx * cos_y - ty * sin_y,
            unit["pos"][1] + tx * sin_y + ty * cos_y,
            tz,
        ],
        yaw,
    )


def _spec_for(category: str, meds: dict) -> str:
    """S1's spec disambiguator, DERIVED from meds.toml (ADR-15): the box
    dimensions in mm — deterministic and distinct per product."""
    size_mm = "x".join(str(round(s * 1000)) for s in meds[category]["size"])
    return f"{size_mm} mm box"


def generate_episode(seed: int, scenario: str) -> dict:
    """RS-3: the seeded oracle task description for one episode, published
    per TC-7 as the episode goal. Pure in (seed, scenario) — CON-5."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; known: {list(SCENARIOS)}")
    plano = load_planogram()
    meds = load_meds()
    rng = random.Random(f"{scenario}:{seed}")
    goal: dict = {"scenario": scenario, "seed": seed}

    if scenario == "S1":
        stock: dict[str, int] = {}
        for slot in plano["slots"].values():
            stock[slot["category"]] = stock.get(slot["category"], 0) + slot["capacity"]
        products = rng.sample(sorted(stock), 2)
        goal["order"] = [
            {
                "product": product,
                "spec": _spec_for(product, meds),
                "qty": rng.randint(1, min(3, stock[product])),
            }
            for product in products
        ]
    elif scenario == "S2":
        slots = rng.sample(sorted(plano["slots"]), 2)
        goal["restock"] = [
            {"slot": slot_id, "category": plano["slots"][slot_id]["category"]} for slot_id in slots
        ]
    else:  # S3
        slot_ids = sorted(plano["slots"])
        while True:  # bounded in practice: most pairs differ in category
            slot_a, slot_b = rng.sample(slot_ids, 2)
            if plano["slots"][slot_a]["category"] != plano["slots"][slot_b]["category"]:
                break
        goal["misplaced"] = [
            {"item": f"{slot_a}#0", "found_in": slot_b, "belongs_in": slot_a},
            {"item": f"{slot_b}#0", "found_in": slot_a, "belongs_in": slot_b},
        ]
    return goal


@dataclass(frozen=True)
class StoreItem:
    """One physical product instance: where it spawns and what it is.
    slot_id is a planogram slot, or "bin" for restocking-bin stock."""

    item_id: str
    category: str
    slot_id: str


def stocked_items(plano: dict, episode: dict) -> list[StoreItem]:
    """RS-2/RS-3: the deterministic stock list the scene is generated
    from — planogram file order (ADR-15), perturbed by the episode: S2
    empties its slots, S3 swaps its two items. The bin always holds one
    item of every category (ADR-15) so S2 is always restockable."""
    empty = {entry["slot"] for entry in episode.get("restock", [])}
    swapped = {entry["item"]: entry["found_in"] for entry in episode.get("misplaced", [])}
    items: list[StoreItem] = []
    for slot_id, slot in plano["slots"].items():
        if slot_id in empty:
            continue
        for k in range(slot["capacity"]):
            item_id = f"{slot_id}#{k}"
            items.append(StoreItem(item_id, slot["category"], swapped.get(item_id, slot_id)))
    for category in load_meds():
        items.append(StoreItem(f"bin#{category}", category, "bin"))
    return items


@dataclass
class StoreHandle:
    scene: Any
    robot: Any
    items: dict[str, Any]  # item_id -> genesis entity, stock order
    categories: dict[str, str]  # item_id -> category
    counter: Any
    bin: Any
    cams: dict[str, Any]
    planogram: dict
    episode: dict
    embodiment: str
    seed: int
    scenario: str
    med_sizes: dict[str, list[float]] = field(default_factory=dict)


def yaw_quat_wxyz(yaw: float) -> tuple[float, float, float, float]:
    """A pure-yaw rotation as a genesis morph quaternion (w-x-y-z — note:
    genesis convention, NOT the TC-1 x-y-z-w wire order). PR #18 review:
    entities must carry the planogram's composed world yaw physically, so
    the scene agrees with its own 7D template poses (RS-1) and the RS-4
    yaw/front-face checks read true orientations."""
    half = yaw / 2
    return (math.cos(half), 0.0, 0.0, math.sin(half))


def build_store(
    seed: int,
    scenario: str,
    embodiment: str = "mobile",
    n_envs: int = 1,
    headless: bool = True,
) -> StoreHandle:
    """RS-2: build the store — >=3 shelf units in >=2 aisles, delivery
    counter, restocking bin — GENERATED from planogram.toml, stocked per
    the seeded episode (RS-3). Reuses build_scene conventions (SCN-1
    purity, toml-driven assets, mobile profile from physics.toml)."""
    gs = _ensure_genesis()
    meds = load_meds()
    physics = load_physics()
    plano = load_planogram()
    episode = generate_episode(seed, scenario)
    store, geo = plano["store"], plano["store"]["unit_geometry"]
    profile = physics["embodiment"][embodiment]

    import numpy as np

    scene = gs.Scene(
        sim_options=gs.options.SimOptions(
            dt=physics["sim"]["dt"],
            substeps=physics["sim"]["substeps"],
            gravity=tuple(physics["sim"]["gravity"]),
        ),
        rigid_options=gs.options.RigidOptions(enable_neutral_collision=True),
        vis_options=gs.options.VisOptions(
            ambient_light=(physics["domain_randomization"]["ambient_default"],) * 3
        ),
        renderer=gs.renderers.Rasterizer(),  # SCN-5: Metal-safe default path
        show_viewer=not headless,
    )
    scene.add_entity(gs.morphs.Plane())

    shelf_material = gs.materials.Rigid(friction=physics["materials"]["shelf"]["friction"])
    for unit in plano["units"].values():
        for level_height in geo["level_heights"]:
            scene.add_entity(
                gs.morphs.Box(
                    size=(geo["depth"], geo["width"], geo["board_thickness"]),
                    pos=(unit["pos"][0], unit["pos"][1], level_height),
                    quat=yaw_quat_wxyz(unit["yaw"]),
                    fixed=True,
                ),
                material=shelf_material,
            )

    tray_material = gs.materials.Rigid(friction=physics["materials"]["tray"]["friction"])
    counter = scene.add_entity(
        gs.morphs.Box(
            size=tuple(store["counter_size"]), pos=tuple(store["counter_pos"]), fixed=True
        ),
        material=tray_material,
    )
    bin_entity = scene.add_entity(
        gs.morphs.Box(size=tuple(store["bin_size"]), pos=tuple(store["bin_pos"]), fixed=True),
        material=tray_material,
    )

    robot = scene.add_entity(gs.morphs.MJCF(file=FRANKA_MJCF))

    box_physics = physics["materials"]["box"]
    bin_top = store["bin_pos"][2] + store["bin_size"][2] / 2
    bin_row = sorted(load_meds())  # deterministic bin arrangement (ADR-15)
    items: dict[str, Any] = {}
    categories: dict[str, str] = {}
    for item in stocked_items(plano, episode):
        size = tuple(meds[item.category]["size"])
        if item.slot_id == "bin":
            k = bin_row.index(item.category)
            span = store["bin_size"][1] - max(s["size"][1] for s in meds.values())
            y = store["bin_pos"][1] - span / 2 + k * span / max(1, len(bin_row) - 1)
            pos = (store["bin_pos"][0], y, bin_top + size[2] / 2)
            yaw = 0.0
        else:
            world, yaw = slot_world_pose(plano, item.slot_id)
            pos = (world[0], world[1], world[2] + size[2] / 2)
        categories[item.item_id] = item.category
        # PR #18 review: spawn the ORIGINAL dimensions with the composed
        # world yaw as a physical rotation — the entity quaternion must
        # agree with the planogram's 7D template pose (RS-1/RS-4)
        items[item.item_id] = scene.add_entity(
            gs.morphs.Box(size=size, pos=pos, quat=yaw_quat_wxyz(yaw)),
            material=gs.materials.Rigid(
                friction=box_physics["friction"], rho=box_physics["density_kg_m3"]
            ),
            surface=gs.surfaces.Default(color=tuple(meds[item.category]["color"])),
        )

    cam_cfg = physics["cameras"]
    cams = {
        "overhead": scene.add_camera(
            res=(640, 480),
            pos=tuple(cam_cfg["store_overhead_pos"]),
            lookat=tuple(cam_cfg["store_overhead_lookat"]),
            fov=70,
            GUI=False,
        ),
        "wrist": scene.add_camera(res=(320, 240), fov=70, GUI=False),
    }

    if n_envs == 1:
        scene.build()
    else:
        scene.build(n_envs=n_envs)

    home = np.asarray(profile["home_qpos"], dtype=np.float32)
    robot.set_qpos(home if n_envs == 1 else np.tile(home, (n_envs, 1)))
    count = int(profile["gripper_dofs"])
    finger_dofs = list(range(robot.n_dofs - count, robot.n_dofs))
    robot.set_dofs_kp(
        np.asarray(profile["gripper_kp"], dtype=np.float32), dofs_idx_local=finger_dofs
    )
    robot.set_dofs_kv(
        np.asarray(profile["gripper_kv"], dtype=np.float32), dofs_idx_local=finger_dofs
    )

    ee_link = robot.get_link("hand")
    offset = np.eye(4, dtype=np.float32)
    offset[:3, 3] = cam_cfg["wrist_offset_m"]
    cams["wrist"].attach(ee_link, offset_T=offset)

    # NO global reach assert (ADR-15): reach is relative to the MOBILE base,
    # which navigates between units — slot reachability is per-episode.
    return StoreHandle(
        scene=scene,
        robot=robot,
        items=items,
        categories=categories,
        counter=counter,
        bin=bin_entity,
        cams=cams,
        planogram=plano,
        episode=episode,
        embodiment=embodiment,
        seed=seed,
        scenario=scenario,
        med_sizes={name: list(meds[name]["size"]) for name in meds},
    )
