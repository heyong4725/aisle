"""Unit tests for the return planner's pure geometry (T4 inc-2,
VER-3 amendment `return_item`; CON-12 — no sim, no dora)."""

import pytest

from aisle.nodes.return_planner import return_grasp_and_slot
from aisle.scenes.pharmacy import load_meds, load_physics, resolve_layout

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def geometry():
    return load_meds(), resolve_layout(load_physics(), "franka")


def test_grasp_is_at_the_tray_at_the_meds_half_height(geometry):
    meds, layout = geometry
    grasp, _ = return_grasp_and_slot("amoxicillin", meds, layout)
    tray = layout["tray"]
    assert grasp[0] == pytest.approx(tray["pos"][0])
    assert grasp[1] == pytest.approx(tray["pos"][1])
    tray_top = tray["pos"][2] + tray["size"][2] / 2.0
    assert grasp[2] == pytest.approx(tray_top + meds["amoxicillin"]["size"][2] / 2.0)
    assert grasp[3:] == [0.0, 0.0, 0.0, 1.0]


def test_return_slot_is_inside_the_shelf_front_corner(geometry):
    meds, layout = geometry
    _, (sx, sy) = return_grasp_and_slot("metformin", meds, layout)
    shelf = layout["shelf"]
    assert abs(sx - shelf["pos"][0]) <= shelf["level_size"][0] / 2.0
    assert abs(sy - shelf["pos"][1]) <= shelf["level_size"][1] / 2.0


def test_unknown_med_refuses(geometry):
    meds, layout = geometry
    assert return_grasp_and_slot("aspirin", meds, layout) is None
