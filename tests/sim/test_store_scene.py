"""Sim acceptance for the store scene (SPEC 200 RS-1..3) — the modules
named by the spec.

Marker `sim`: imports genesis, runs headless (CON-12). Run via
`uv sync --extra sim && uv run pytest -m sim`.
"""

import importlib.util
import math

import pytest

from aisle.scenes.pharmacy import to_numpy
from aisle.scenes.store import (
    build_store,
    generate_episode,
    load_planogram,
    slot_world_pose,
    stocked_items,
)

# find_spec keeps collection sim-free: genesis is only executed inside tests
pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None, reason="sim extra not installed"
    ),
]


def _xyz(entity) -> list[float]:
    return [float(v) for v in to_numpy(entity.get_pos()).reshape(-1)[:3]]


def _quat_wxyz(entity) -> list[float]:
    return [float(v) for v in to_numpy(entity.get_quat()).reshape(-1)[:4]]


def _assert_yaw(entity, yaw: float, label) -> None:
    """The entity's PHYSICAL orientation matches the composed world yaw
    (PR #18 review): quaternion equal up to sign, and the rotated +x
    (front-face) direction points where the slot faces."""
    from aisle.scenes.store import yaw_quat_wxyz

    got = _quat_wxyz(entity)
    want = list(yaw_quat_wxyz(yaw))
    if got[0] * want[0] + got[3] * want[3] < 0:  # q and -q are the same rotation
        want = [-c for c in want]
    assert got == pytest.approx(want, abs=1e-5), (label, got, want)
    # front direction: rotate body +x by the yaw
    w, _, _, z = _quat_wxyz(entity)
    front = (1 - 2 * z * z, 2 * w * z)
    assert front[0] == pytest.approx(math.cos(yaw), abs=1e-5), label
    assert front[1] == pytest.approx(math.sin(yaw), abs=1e-5), label


def test_planogram_generation():
    """RS-1, RS-2: the built store is GENERATED from planogram.toml — one
    item per stocked slot AT its slot's world template pose (z = board
    surface + half the item height), >= 3 units in >= 2 aisles worth of
    boards, counter and bin present, the mobile-profile robot at its
    store-frame start, and the item order deterministic (planogram file
    order, SCN-1 analog / CON-5)."""
    handle = build_store(seed=0, scenario="S1")
    plano = handle.planogram

    # every stocked slot's item sits at its template pose
    expected_ids = [item.item_id for item in stocked_items(plano, handle.episode)]
    assert list(handle.items) == expected_ids  # deterministic stock order
    for slot_id, slot in plano["slots"].items():
        item_id = f"{slot_id}#0"
        assert item_id in handle.items, f"slot {slot_id} not stocked"
        world, yaw = slot_world_pose(plano, slot_id)
        size_z = handle.med_sizes[slot["category"]][2]
        pos = _xyz(handle.items[item_id])
        assert pos[0] == pytest.approx(world[0], abs=1e-4), slot_id
        assert pos[1] == pytest.approx(world[1], abs=1e-4), slot_id
        assert pos[2] == pytest.approx(world[2] + size_z / 2, abs=1e-4), slot_id
        # RS-1/RS-4 (PR #18 review): the item's PHYSICAL quaternion and
        # front-face direction agree with the slot's composed world yaw
        _assert_yaw(handle.items[item_id], yaw, slot_id)

    # bin stock: one item of every category resting on the bin's top
    store = plano["store"]
    bin_top = store["bin_pos"][2] + store["bin_size"][2] / 2
    bin_items = [i for i in handle.items if i.startswith("bin#")]
    assert len(bin_items) == len(handle.med_sizes)
    for item_id in bin_items:
        pos = _xyz(handle.items[item_id])
        size_z = handle.med_sizes[handle.categories[item_id]][2]
        assert pos[2] == pytest.approx(bin_top + size_z / 2, abs=1e-4), item_id

    # counter and bin entities exist where the planogram puts them
    assert _xyz(handle.counter)[:2] == pytest.approx(store["counter_pos"][:2], abs=1e-4)
    assert _xyz(handle.bin)[:2] == pytest.approx(store["bin_pos"][:2], abs=1e-4)

    # RS-2 store shape: >= 3 units across >= 2 zones (source of the boards)
    assert len(plano["units"]) >= 3
    assert len({u["zone"] for u in plano["units"].values()}) >= 2

    # the mobile robot is present at the store-frame origin (base_start)
    base = _xyz(handle.robot)
    assert math.hypot(base[0], base[1]) < 0.05
    assert handle.embodiment == "mobile"


def test_episode_generators_seeded():
    """RS-3 (CON-5): the seeded generators drive the BUILT scene — S2's two
    assigned slots are empty (their items absent, everything else stocked)
    and S3's two swapped items physically sit in each other's slots; the
    generators themselves are deterministic per (seed, scenario)."""
    for scenario in ("S1", "S2", "S3"):
        assert generate_episode(11, scenario) == generate_episode(11, scenario)

    plano = load_planogram()

    s2 = build_store(seed=4, scenario="S2")
    empty = {entry["slot"] for entry in s2.episode["restock"]}
    assert len(empty) == 2
    for slot_id in plano["slots"]:
        present = f"{slot_id}#0" in s2.items
        assert present == (slot_id not in empty), slot_id

    s3 = build_store(seed=4, scenario="S3")
    entries = s3.episode["misplaced"]
    assert len(entries) == 2
    for entry in entries:
        # the item spawned at the slot it was FOUND IN, not its home
        world, _ = slot_world_pose(plano, entry["found_in"])
        pos = _xyz(s3.items[entry["item"]])
        assert pos[0] == pytest.approx(world[0], abs=1e-4), entry
        assert pos[1] == pytest.approx(world[1], abs=1e-4), entry
        # and it IS a misplacement: item category != found-in slot category
        item_cat = s3.categories[entry["item"]]
        assert item_cat != plano["slots"][entry["found_in"]]["category"], entry
