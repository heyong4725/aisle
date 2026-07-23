"""Unit tests for the store planogram and episode generators (SPEC 200
RS-1..3). Pure config/logic only — no sim, no genesis import (CON-12);
the generated-scene half of the acceptance lives in
tests/sim/test_store_scene.py.
"""

import math

import pytest
from cli_helpers import run_cli

pytestmark = pytest.mark.unit

SCENARIOS = ("S1", "S2", "S3")


def _planogram():
    from aisle.scenes.store import load_planogram

    return load_planogram()


def _meds():
    from aisle.scenes.pharmacy import load_meds

    return load_meds()


class TestPlanogram:
    def test_planogram_config(self):
        """RS-1: every slot maps to {category, template_pose(7d),
        capacity, shelf_zone}; categories are med names (ADR-15); the
        template quat is TC-1 (x,y,z,w) unit; slot ids follow the
        <unit>-L<level>-S<idx> scheme and zones match their unit's."""
        plano = _planogram()
        meds = _meds()
        units = plano["units"]
        assert plano["slots"], "planogram has no slots"
        for slot_id, slot in plano["slots"].items():
            assert set(slot) == {"category", "template_pose", "capacity", "shelf_zone"}, slot_id
            assert slot["category"] in meds, (slot_id, slot["category"])
            pose = slot["template_pose"]
            assert len(pose) == 7, slot_id
            assert math.hypot(*pose[3:]) == pytest.approx(1.0, abs=1e-6), slot_id
            assert slot["capacity"] >= 1, slot_id
            unit = slot_id.split("-")[0]
            assert unit in units, slot_id
            assert slot["shelf_zone"] == units[unit]["zone"], slot_id

    def test_every_category_is_orderable(self):
        """ADR-15: every category has >= 3 stocked slots, so an S1 qty of
        1..3 is always fulfillable from the shelves."""
        plano = _planogram()
        counts: dict[str, int] = {}
        for slot in plano["slots"].values():
            counts[slot["category"]] = counts.get(slot["category"], 0) + slot["capacity"]
        for category, count in sorted(counts.items()):
            assert count >= 3, (category, count)

    def test_store_layout_navigable(self):
        """RS-2: >= 3 shelf units in >= 2 aisles; the corridor between
        rows in different zones is >= aisle_min_width_m; the counter and
        bin footprints overlap no unit footprint."""
        plano = _planogram()
        store, units = plano["store"], plano["units"]
        assert len(units) >= 3
        zones = {u["zone"] for u in units.values()}
        assert len(zones) >= 2

        geo = store["unit_geometry"]
        min_width = store["aisle_min_width_m"]

        def footprint(unit):
            # v0 units are axis-aligned at yaw 0/±pi/2/pi (ADR-15): the
            # footprint is width x depth, swapped when rotated 90 degrees
            swap = abs(math.sin(unit["yaw"])) > 0.5
            half_x = (geo["width"] if swap else geo["depth"]) / 2
            half_y = (geo["depth"] if swap else geo["width"]) / 2
            x, y = unit["pos"]
            return x - half_x, x + half_x, y - half_y, y + half_y

        # corridor: min gap between unit footprints in DIFFERENT zones
        items = list(units.values())
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if a["zone"] == b["zone"]:
                    continue
                ax0, ax1, ay0, ay1 = footprint(a)
                bx0, bx1, by0, by1 = footprint(b)
                gap_x = max(bx0 - ax1, ax0 - bx1, 0.0)
                gap_y = max(by0 - ay1, ay0 - by1, 0.0)
                assert max(gap_x, gap_y) >= min_width, (a, b)

        # counter/bin stay clear of every unit footprint
        for label in ("counter", "bin"):
            pos, size = store[f"{label}_pos"], store[f"{label}_size"]
            fx0, fx1 = pos[0] - size[0] / 2, pos[0] + size[0] / 2
            fy0, fy1 = pos[1] - size[1] / 2, pos[1] + size[1] / 2
            for unit in items:
                ux0, ux1, uy0, uy1 = footprint(unit)
                overlap = fx0 < ux1 and ux0 < fx1 and fy0 < uy1 and uy0 < fy1
                assert not overlap, (label, unit)

    def test_slot_world_pose_transform(self):
        """ADR-15: world pose = unit yaw/translation ∘ template. A1 faces
        -y (yaw -pi/2), so its slot template x=+0.05 lands 0.05 toward the
        aisle (y = 1.6 - 0.05) and the template y offset lands along x."""
        from aisle.scenes.store import slot_world_pose

        plano = _planogram()
        pos, yaw = slot_world_pose(plano, "A1-L0-S1")  # template [0.05, 0, 0.06]
        assert pos[0] == pytest.approx(1.0, abs=1e-6)
        assert pos[1] == pytest.approx(1.55, abs=1e-6)
        assert pos[2] == pytest.approx(0.06, abs=1e-6)
        assert yaw == pytest.approx(-1.5708, abs=1e-6)
        pos_b, _ = slot_world_pose(plano, "B1-L1-S0")  # template y=-0.22, unit yaw +pi/2
        assert pos_b[2] == pytest.approx(0.36, abs=1e-6)

    def test_nav_locations_align_with_store(self):
        """MOB-2/ADR-15: the named nav targets point AT the store — the
        counter/bin locations stand within a metre of the counter/bin
        objects, and both shelf zones have a named location."""
        from aisle.mobility.nav import load_locations

        plano = _planogram()
        locations = load_locations()
        for label in ("counter", "bin"):
            loc = locations[label]
            obj = plano["store"][f"{label}_pos"]
            assert math.hypot(loc[0] - obj[0], loc[1] - obj[1]) <= 1.0, label
        zones = {u["zone"] for u in plano["units"].values()}
        for zone in zones:
            assert f"shelf_zone_{zone[-1].upper()}" in locations, zone


class TestEpisodeGenerators:
    """RS-3: seeded, pure generators; output is the episode's oracle task
    description (published per TC-7 goal)."""

    def _gen(self, seed, scenario):
        from aisle.scenes.store import generate_episode

        return generate_episode(seed, scenario)

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_deterministic_and_seed_sensitive(self, scenario):
        """CON-5: same seed ⇒ identical description; different seeds ⇒
        different descriptions (over a few seeds)."""
        assert self._gen(7, scenario) == self._gen(7, scenario)
        assert any(self._gen(7, scenario) != self._gen(s, scenario) for s in (8, 9, 10))

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_goal_carries_scenario_and_seed(self, scenario):
        goal = self._gen(3, scenario)
        assert goal["scenario"] == scenario
        assert goal["seed"] == 3

    def test_s1_order_shape(self):
        """RS-3 (S1): 2 distinct product types, qty 1..3 each, with a
        nonempty spec disambiguator, all fulfillable from shelf stock."""
        plano = _planogram()
        stock: dict[str, int] = {}
        for slot in plano["slots"].values():
            stock[slot["category"]] = stock.get(slot["category"], 0) + slot["capacity"]
        for seed in range(20):
            order = self._gen(seed, "S1")["order"]
            assert len(order) == 2
            products = [line["product"] for line in order]
            assert len(set(products)) == 2
            for line in order:
                assert line["product"] in stock
                assert 1 <= line["qty"] <= 3
                assert line["qty"] <= stock[line["product"]]
                assert isinstance(line["spec"], str) and line["spec"]
            # the spec disambiguates: distinct products, distinct specs
            assert order[0]["spec"] != order[1]["spec"]

    def test_s2_destock_shape(self):
        """RS-3 (S2): exactly 2 distinct slots de-stocked, each reported
        with its planogram category."""
        plano = _planogram()
        for seed in range(20):
            restock = self._gen(seed, "S2")["restock"]
            assert len(restock) == 2
            slots = [entry["slot"] for entry in restock]
            assert len(set(slots)) == 2
            for entry in restock:
                assert entry["category"] == plano["slots"][entry["slot"]]["category"]

    def test_s3_swap_is_a_real_misplacement(self):
        """RS-3 (S3): 2 items swapped between slots of DIFFERENT
        categories (else nothing is misplaced); each entry names the item,
        where it now sits, and its planogram home."""
        plano = _planogram()
        for seed in range(20):
            misplaced = self._gen(seed, "S3")["misplaced"]
            assert len(misplaced) == 2
            a, b = misplaced
            assert a["found_in"] == b["belongs_in"] and b["found_in"] == a["belongs_in"]
            cat_a = plano["slots"][a["belongs_in"]]["category"]
            cat_b = plano["slots"][b["belongs_in"]]["category"]
            assert cat_a != cat_b, (seed, a, b)
            for entry in misplaced:
                assert entry["item"].startswith(entry["belongs_in"] + "#")

    def test_unknown_scenario_is_rejected(self):
        """Loud error, never a silent default (CON-8 spirit)."""
        from aisle.scenes.store import generate_episode

        with pytest.raises(ValueError, match="S9"):
            generate_episode(0, "S9")


def test_stocked_items_reflect_episode():
    """RS-2/RS-3: the stock list the scene is GENERATED from honors the
    episode — S2 empties exactly the assigned slots, S3 swaps exactly the
    two items' slots, and the bin always holds one item per category."""
    from aisle.scenes.store import generate_episode, load_planogram, stocked_items

    plano = load_planogram()
    meds = _meds()
    baseline = stocked_items(plano, generate_episode(0, "S1"))
    by_id = {item.item_id: item for item in baseline}
    assert len(by_id) == len(baseline)  # unique ids
    shelf_items = [i for i in baseline if i.slot_id != "bin"]
    assert len(shelf_items) == sum(s["capacity"] for s in plano["slots"].values())
    bin_items = [i for i in baseline if i.slot_id == "bin"]
    assert sorted(i.category for i in bin_items) == sorted(meds)

    ep2 = generate_episode(4, "S2")
    empty = {e["slot"] for e in ep2["restock"]}
    s2_items = stocked_items(plano, ep2)
    assert all(i.slot_id not in empty for i in s2_items)
    assert len(s2_items) == len(baseline) - len(empty)

    ep3 = generate_episode(4, "S3")
    a, b = ep3["misplaced"]
    s3 = {i.item_id: i for i in stocked_items(plano, ep3)}
    assert s3[a["item"]].slot_id == a["found_in"]
    assert s3[b["item"]].slot_id == b["found_in"]


def test_module_import_stays_sim_free():
    """CON-12: importing aisle.scenes.store must not import genesis."""
    probe = (
        "import sys; import aisle.scenes.store; "
        "assert 'genesis' not in sys.modules, 'genesis imported at module level'"
    )
    proc = run_cli(["-c", probe])
    assert proc.returncode == 0, proc.stderr
