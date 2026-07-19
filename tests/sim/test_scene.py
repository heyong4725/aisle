"""Sim acceptance tests for the pharmacy scene (SPEC 020 SCN-1, SCN-3..5, SCN-7).

Marker `sim`: imports genesis, runs headless (CON-12). Run via
`uv sync --extra sim && uv run pytest -m sim`.
"""

import importlib.util

import numpy as np
import pytest

from aisle.scenes.pharmacy import (
    MED_NAMES,
    DRToggle,
    SceneCfg,
    build_scene,
    load_physics,
    oracle_state,
    resolve_layout,
    to_numpy,
)

# find_spec keeps collection sim-free: genesis is only executed inside tests
pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None, reason="sim extra not installed"
    ),
]


@pytest.fixture(scope="module")
def handle():
    return build_scene(seed=7, embodiment="franka", n_envs=1, headless=True)


def test_build_determinism(handle):
    """SCN-1, SCN-7: build_scene is a pure function of its arguments — the
    same (seed, cfg, platform) yields a bitwise-identical initial
    oracle_state; a different seed yields a different one."""
    first = oracle_state(handle)
    again = oracle_state(build_scene(seed=7, embodiment="franka", n_envs=1, headless=True))
    other = oracle_state(build_scene(seed=11, embodiment="franka", n_envs=1, headless=True))
    assert first.dtype == np.float32
    assert first.shape == (len(MED_NAMES) * 7,)
    assert np.array_equal(first, again)  # bitwise
    assert not np.array_equal(first, other)


def test_reachability(handle):
    """SCN-3, SCN-4: every sampled box placement admits a deterministic IK
    solution (asserted inside build_scene, position AND rotation), and the
    tray and shelf sit inside the franka layout profile's reach."""
    layout = resolve_layout(load_physics(), "franka")
    base = to_numpy(handle.robot.get_pos()).reshape(-1)[:3]
    for target in (layout["tray"]["pos"], layout["shelf"]["pos"]):
        assert np.linalg.norm(np.asarray(target) - base) <= layout["reach_m"]
    assert handle.reachability_errors == []


def test_no_interpenetration(handle):
    """SCN-3: initial box placements are rejection-sampled free of
    interpenetration — pairwise AABBs of the five boxes do not overlap."""
    aabbs = []
    for name, entity in handle.boxes.items():
        pos = to_numpy(entity.get_pos()).reshape(-1)[:3]
        half = np.asarray(handle.med_sizes[name]) / 2.0
        aabbs.append((name, pos - half, pos + half))
    for i, (name_a, lo_a, hi_a) in enumerate(aabbs):
        for name_b, lo_b, hi_b in aabbs[i + 1 :]:
            overlap = np.all(lo_a < hi_b) and np.all(lo_b < hi_a)
            assert not overlap, (name_a, name_b)


def test_cameras(handle):
    """SCN-5: overhead 640x480 fov 55 fixed; wrist 320x240 fov 70 ATTACHED
    to the EE link; the scene renders on the rasterizer path."""
    assert set(handle.cams) == {"overhead", "wrist"}
    overhead, wrist = handle.cams["overhead"], handle.cams["wrist"]
    assert tuple(overhead.res) == (640, 480) and overhead.fov == 55
    assert tuple(wrist.res) == (320, 240) and wrist.fov == 70
    assert getattr(wrist, "_attached_link", None) is not None
    assert type(handle.scene.visualizer.renderer).__name__ == "Rasterizer"


def test_boxes_follow_oracle_order(handle):
    """SCN-1: boxes dict insertion order is the fixed meds.toml order, which
    is the oracle_state layout (TC table)."""
    assert list(handle.boxes) == MED_NAMES


def test_dr_toggles_are_effective_seeded_and_isolated(handle):
    """SCN-6: an enabled toggle actually changes its axis, the same toggle
    seed reproduces the same values, a different seed differs — and box
    placements (oracle_state) stay untouched throughout."""
    cfg_a = SceneCfg(friction_jitter=DRToggle(enabled=True, seed=3))
    jittered_a = build_scene(seed=7, embodiment="franka", headless=True, cfg=cfg_a)
    jittered_b = build_scene(seed=7, embodiment="franka", headless=True, cfg=cfg_a)
    jittered_c = build_scene(
        seed=7,
        embodiment="franka",
        headless=True,
        cfg=SceneCfg(friction_jitter=DRToggle(enabled=True, seed=4)),
    )
    base_frictions = handle.dr_applied["frictions"]
    assert jittered_a.dr_applied["frictions"] != base_frictions  # effective
    assert jittered_a.dr_applied["frictions"] == jittered_b.dr_applied["frictions"]  # seeded
    assert jittered_a.dr_applied["frictions"] != jittered_c.dr_applied["frictions"]  # per-seed
    assert np.array_equal(oracle_state(handle), oracle_state(jittered_a))  # isolated

    lit = build_scene(
        seed=7,
        embodiment="franka",
        headless=True,
        cfg=SceneCfg(lighting=DRToggle(enabled=True, seed=5)),
    )
    assert lit.dr_applied["ambient"] != handle.dr_applied["ambient"]
    shaken = build_scene(
        seed=7,
        embodiment="franka",
        headless=True,
        cfg=SceneCfg(camera_jitter=DRToggle(enabled=True, seed=6)),
    )
    assert shaken.dr_applied["overhead_pos"] != handle.dr_applied["overhead_pos"]


def test_so101_requires_asset():
    """SCN-4: the so101 embodiment builds from assets/so101/ with its own
    layout profile; without the asset the failure is the explicit
    FileNotFoundError (skipped until the asset lands, ADR-6)."""
    from aisle.scenes.pharmacy import SO101_URDF

    if not SO101_URDF.exists():
        with pytest.raises(FileNotFoundError, match="so101 asset missing"):
            build_scene(seed=7, embodiment="so101", headless=True)
        pytest.skip("assets/so101 not present (acquisition pending human sign-off, ADR-6)")
    handle = build_scene(seed=7, embodiment="so101", headless=True)
    assert list(handle.boxes) == MED_NAMES
