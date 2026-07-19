"""Unit tests for the pharmacy scene configuration (SPEC 020 SCN-2, SCN-6).

Config parsing and the pure placement sampler only — no sim, no genesis
import (CON-12 unit marker).
"""

import dataclasses
import itertools
import tomllib

import pytest
from conftest import REPO_ROOT, run_cli

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
        assert len(profile["shelf_level_heights"]) == 3
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
    different seeds, all on the 3-level shelf."""
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


@pytest.mark.parametrize("embodiment", ["franka", "so101"])
def test_placements_never_interpenetrate_or_exceed_reach(embodiment):
    """SCN-3: across 200 seeds (including the seed-99 regression the review
    found), no two same-level boxes overlap on BOTH axes, and every
    pre-grasp target respects the reach pre-filter."""
    from aisle.scenes.pharmacy import load_physics as load_p
    from aisle.scenes.pharmacy import resolve_layout, sample_placements

    physics = load_p()
    layout = resolve_layout(physics, embodiment)
    meds = load_meds()
    ik = layout["ik"]
    max_target = layout["reach_m"] * ik["reach_margin_frac"]
    for seed in range(200):
        placements = sample_placements(seed, MED_NAMES, layout)
        for p in placements:
            target = (p.x**2 + p.y**2 + (p.z + ik["pregrasp_height_m"]) ** 2) ** 0.5
            assert target <= max_target, (seed, p)
        for a, b in itertools.combinations(placements, 2):
            if a.level != b.level:
                continue
            half_x = (meds[a.name]["size"][0] + meds[b.name]["size"][0]) / 2
            half_y = (meds[a.name]["size"][1] + meds[b.name]["size"][1]) / 2
            overlap = abs(a.x - b.x) < half_x and abs(a.y - b.y) < half_y
            assert not overlap, (seed, a.name, b.name)


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
