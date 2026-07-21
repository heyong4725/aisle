"""Unit tests for the pharmacy scene configuration (SPEC 020 SCN-2, SCN-6).

Config parsing and the pure placement sampler only — no sim, no genesis
import (CON-12 unit marker).
"""

import dataclasses
import itertools
import tomllib

import pytest
from cli_helpers import REPO_ROOT, run_cli

pytestmark = pytest.mark.unit

SCENES = REPO_ROOT / "src" / "aisle" / "scenes"
MED_NAMES = ["amoxicillin", "ibuprofen", "cetirizine", "omeprazole", "metformin"]


def load_meds() -> dict:
    with open(SCENES / "meds.toml", "rb") as f:
        return tomllib.load(f)


def load_physics() -> dict:
    with open(SCENES / "physics.toml", "rb") as f:
        return tomllib.load(f)


def test_med_config():
    """SCN-2: exactly the five fixed medicine names, in oracle_state order,
    each with a 3-vector size in meters and an RGBA color."""
    meds = load_meds()
    assert list(meds) == MED_NAMES
    for name, spec in meds.items():
        assert len(spec["size"]) == 3 and all(0 < s < 0.3 for s in spec["size"]), name
        assert len(spec["color"]) == 4 and all(0 <= c <= 1 for c in spec["color"]), name


def test_physics_config():
    """SCN-2, SCN-4: physics and layout constants live in physics.toml —
    materials, sim step, shared shelf geometry, and a per-embodiment layout
    profile whose shelf and tray are inside that embodiment's reach."""
    physics = load_physics()
    assert physics["sim"]["dt"] > 0
    for material in ("box", "shelf", "tray"):
        assert physics["materials"][material]["friction"] > 0
    for embodiment in ("franka", "so101"):
        profile = physics["embodiment"][embodiment]
        assert len(profile["shelf_level_heights"]) == 2  # M0 env-change: two levels
        assert profile["reach_m"] > 0
        for key in ("shelf_pos", "tray_pos"):
            distance = sum(c * c for c in profile[key]) ** 0.5
            assert distance <= profile["reach_m"], (embodiment, key)


def test_dr_toggles_default_off():
    """SCN-6: every domain-randomization toggle defaults OFF and each is
    independently seedable; DR distribution constants live in physics.toml,
    not code."""
    from aisle.scenes.pharmacy import SceneCfg

    cfg = SceneCfg()
    toggles = [
        f.name for f in dataclasses.fields(cfg) if type(getattr(cfg, f.name)).__name__ == "DRToggle"
    ]
    assert set(toggles) == {"lighting", "textures", "friction_jitter", "camera_jitter"}
    for name in toggles:
        toggle = getattr(cfg, name)
        assert toggle.enabled is False
        assert isinstance(toggle.seed, int)  # independently seedable
    dr = load_physics()["domain_randomization"]
    assert {"friction_jitter_frac", "camera_jitter_m", "ambient_min", "texture_scale_min"} <= set(
        dr
    )


def test_placement_sampler_deterministic():
    """SCN-1, SCN-3 (CON-5): the placement sampler is a pure function of its
    seed — identical placements for identical seeds, different for
    different seeds, across the shelf levels."""
    from aisle.scenes.pharmacy import load_physics as load_p
    from aisle.scenes.pharmacy import resolve_layout, sample_placements

    layout = resolve_layout(load_p(), "franka")
    a = sample_placements(seed=7, med_names=MED_NAMES, layout=layout)
    b = sample_placements(seed=7, med_names=MED_NAMES, layout=layout)
    c = sample_placements(seed=8, med_names=MED_NAMES, layout=layout)
    assert a == b
    assert a != c
    assert [p.name for p in a] == MED_NAMES
    for p in a:
        assert 0 <= p.level < 3


@pytest.fixture(scope="module")
def placements_200(request):
    """One generation pass of 200 seeds x both embodiments, shared by the
    sweep tests below (each sample_placements call reparses meds.toml, so
    regenerating per-test doubles the suite's sampler work)."""
    from aisle.scenes.pharmacy import load_physics as load_p
    from aisle.scenes.pharmacy import resolve_layout, sample_placements

    physics = load_p()
    out = {}
    for embodiment in ("franka", "so101"):
        layout = resolve_layout(physics, embodiment)
        out[embodiment] = (
            layout,
            [sample_placements(seed, MED_NAMES, layout) for seed in range(200)],
        )
    return out


@pytest.mark.parametrize("embodiment", ["franka", "so101"])
def test_placements_never_interpenetrate_or_exceed_reach(embodiment, placements_200):
    """SCN-3: across 200 seeds (including the seed-99 regression the review
    found), no two same-level boxes overlap on BOTH axes, and every
    pre-grasp target respects the reach pre-filter."""
    layout, per_seed = placements_200[embodiment]
    meds = load_meds()
    ik = layout["ik"]
    max_target = layout["reach_m"] * ik["reach_margin_frac"]
    for seed, placements in enumerate(per_seed):
        for p in placements:
            target = (p.x**2 + p.y**2 + (p.z + ik["pregrasp_height_m"]) ** 2) ** 0.5
            assert target <= max_target, (seed, p)
        shelf = layout["shelf"]
        for p in placements:
            if p.level + 1 < len(shelf["level_heights"]):
                board_bottom = (
                    shelf["pos"][2]
                    + shelf["level_heights"][p.level + 1]
                    - shelf["board_thickness"] / 2
                )
                box_top = p.z + meds[p.name]["size"][2] / 2
                assert box_top < board_bottom, (seed, p.name, "intersects board above")
        for a, b in itertools.combinations(placements, 2):
            if a.level != b.level:
                continue
            half_x = (meds[a.name]["size"][0] + meds[b.name]["size"][0]) / 2
            half_y = (meds[a.name]["size"][1] + meds[b.name]["size"][1]) / 2
            overlap = abs(a.x - b.x) < half_x and abs(a.y - b.y) < half_y
            assert not overlap, (seed, a.name, b.name)


@pytest.mark.parametrize("embodiment", ["franka", "so101"])
def test_shelf_levels_clear_tallest_box(embodiment):
    """SCN-3: every embodiment's board-to-board clearance fits the tallest
    medicine plus board thickness and separation margin — a box standing on
    any level can never intersect the board above (the so101 regression the
    review found)."""
    physics = load_physics()
    profile = physics["embodiment"][embodiment]
    shelf = physics["shelf"]
    tallest = max(spec["size"][2] for spec in load_meds().values())
    heights = profile["shelf_level_heights"]
    for below, above in zip(heights, heights[1:], strict=False):
        clearance = (above - shelf["board_thickness"] / 2) - (below + shelf["board_thickness"] / 2)
        assert clearance >= tallest + shelf["min_separation"], (embodiment, below, above)


def test_unknown_embodiment_rejected():
    """SCN-4: an embodiment without a layout profile is an explicit error,
    not a KeyError."""
    from aisle.scenes.pharmacy import load_physics as load_p
    from aisle.scenes.pharmacy import resolve_layout

    with pytest.raises(ValueError, match="mobile"):
        resolve_layout(load_p(), "mobile")


def test_module_import_stays_sim_free():
    """CON-12: importing aisle.scenes.pharmacy must not import genesis, so
    unit tests and the validator run without sim dependencies."""
    probe = (
        "import sys; import aisle.scenes.pharmacy; "
        "assert 'genesis' not in sys.modules, 'genesis imported at module level'"
    )
    proc = run_cli(["-c", probe])
    assert proc.returncode == 0, proc.stderr


def test_sampled_boxes_always_have_open_sky(placements_200):
    """SCN-3 / ADR-12: the staggered sampler's open bands and the
    planner's needs_front safety net agree — across 200 seeds and both
    embodiments, NO sampled placement triggers front-mode. The proven
    top-down grasp works on every sampled box; a regression in the band
    math (overhang, HAND_CLEARANCE_M, band-fit guard) surfaces here, not
    in a 4-hour acceptance run."""
    from aisle.nodes.grasp_topdown import needs_front

    for embodiment, (layout, per_seed) in placements_200.items():
        shelf = layout["shelf"]
        for seed, placements in enumerate(per_seed):
            for p in placements:
                assert not needs_front(p.x, p.z, shelf), (embodiment, seed, p)


def test_needs_front_covers_board_span_and_clearance_strip():
    """ADR-12: out-of-band poses under a higher board — including the
    HAND_CLEARANCE_M strip in front of its span, where the T10 physics
    replay showed the hand landing on the board edge — trigger the
    front-mode safety net; open-sky poses do not."""
    from aisle.nodes.grasp_topdown import needs_front
    from aisle.scenes.pharmacy import HAND_CLEARANCE_M, resolve_layout
    from aisle.scenes.pharmacy import load_physics as load_p

    shelf = resolve_layout(load_p(), "franka")["shelf"]
    rear_x = shelf["pos"][0] + shelf["level_size"][0] / 2
    upper_front = rear_x - shelf["level_depths"][1]
    lower_box_z = shelf["pos"][2] + shelf["level_heights"][0] + shelf["board_thickness"] / 2 + 0.05
    upper_box_z = shelf["pos"][2] + shelf["level_heights"][1] + shelf["board_thickness"] / 2 + 0.05
    # directly under the upper board
    assert needs_front(rear_x - 0.01, lower_box_z, shelf)
    # in the reserved clearance strip just in front of the board span
    assert needs_front(upper_front - HAND_CLEARANCE_M / 2, lower_box_z, shelf)
    # in the open band, clear of the strip
    assert not needs_front(upper_front - HAND_CLEARANCE_M - 0.02, lower_box_z, shelf)
    # on the top level there is no board above
    assert not needs_front(rear_x - 0.01, upper_box_z, shelf)
