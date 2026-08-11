"""RST-2 acceptance (SPEC 040, Phase-2 gate): the behavioral reset
returns a delivered box from the tray to a sampled shelf pose using
REALISTIC sensing only, and verifies the placement realistically.

The test drives Genesis directly (sim marker — no dora): the box is
teleported into the tray as SETUP (a privilege of the test, not of the
reset logic, which sees only rendered rgb/depth + config), then the
frozen reset motion runs locate -> plan -> stream -> verify.
"""

import importlib.util

import numpy as np
import pytest

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None, reason="sim extra not installed"
    ),
]


def test_behavioral_reset_returns_the_delivered_box(tmp_path):
    """RST-2: locate (realistic) -> place at the sampled slot -> verify
    (realistic). Metformin at seed 7 — a measured-green combo; the
    attempt/fallback loop over flaky combos is unit-tested in
    test_reset_service.py."""
    from aisle.reset.motion import (
        ResetStreamer,
        locate_box_in_tray,
        place_back_stages,
        placement_verified,
    )
    from aisle.scenes.pharmacy import (
        SceneCfg,
        build_scene,
        load_meds,
        load_physics,
        resolve_layout,
        sample_placements,
        wrist_mount_rotation,
    )
    from aisle.verifier.calibration import build_calibration_v1
    from aisle.verifier.models import load_pinned

    meds = load_meds()
    physics = load_physics()
    layout = resolve_layout(physics, "franka")
    pair = load_pinned("identity")
    handle = build_scene(seed=7, cfg=SceneCfg())
    robot, scene = handle.robot, handle.scene
    n_dof = robot.n_dofs
    target = "metformin"
    box = handle.boxes[target]

    # SETUP privilege: the box sits in the tray as if just delivered
    tray_cfg = layout["tray"]
    tray_top = tray_cfg["pos"][2] + tray_cfg["size"][2] / 2
    height = float(meds[target]["size"][2])
    box.set_pos(
        np.array(
            [tray_cfg["pos"][0], tray_cfg["pos"][1], tray_top + height / 2 + 0.001],
            dtype=np.float32,
        )
    )
    box.set_quat(np.array([1, 0, 0, 0], dtype=np.float32))
    for _ in range(100):
        scene.step()

    cams_cfg = physics["cameras"]
    calibration = build_calibration_v1(
        cams_cfg["overhead_pos"],
        cams_cfg["overhead_lookat"],
        (640, 480),
        55.0,
        cams_cfg["wrist_offset_m"],
        (320, 240),
        70.0,
        wrist_mount_rotation(cams_cfg),
    )
    rgb = np.asarray(handle.cams["overhead"].render(rgb=True)[0])
    depth = np.asarray(handle.cams["overhead"].render(rgb=False, depth=True)[1])

    # realistic localization: rendered pixels + sensor depth, no oracle
    top = locate_box_in_tray(rgb, depth, calibration, list(meds), tray_cfg, model_pair=pair)
    assert top is not None, "the delivered box must be locatable in the tray"
    true_pos = np.asarray(box.get_pos().cpu().numpy(), dtype=np.float64).reshape(-1)[:3]
    assert float(np.linalg.norm(np.asarray(top[:2]) - true_pos[:2])) < 0.02

    place = None
    for placement in sample_placements(13, [target], layout):
        place = np.array([placement.x, placement.y, placement.z])
    shelf = layout["shelf"]
    board_z = shelf["pos"][2] + shelf["level_heights"][0] + shelf["board_thickness"] / 2
    home = np.asarray(physics["embodiment"]["franka"]["home_qpos"], dtype=np.float32)
    stages = place_back_stages(top, tray_top, place, board_z, home)
    assert stages is not None, "the sampled slot must be reachable"

    streamer = ResetStreamer(stages=stages)
    ticks = 0
    while not streamer.done and ticks < 8000:
        qpos = np.asarray(robot.get_qpos().cpu().numpy(), dtype=np.float32).reshape(-1)[:n_dof]
        cmd, _ = streamer.step(qpos)
        if cmd is not None:
            robot.control_dofs_position(cmd)
        scene.step()
        ticks += 1
    assert streamer.done, "the reset stream must terminate (bounded bail per stage)"

    final = np.asarray(box.get_pos().cpu().numpy(), dtype=np.float64).reshape(-1)[:3]
    assert float(np.linalg.norm(final[:2] - place[:2])) < 0.03, (final, place)
    assert final[2] > board_z, "the box must rest ON the shelf board"

    rgb2 = np.asarray(handle.cams["overhead"].render(rgb=True)[0])
    depth2 = np.asarray(handle.cams["overhead"].render(rgb=False, depth=True)[1])
    assert placement_verified(rgb2, depth2, calibration, list(meds), place, model_pair=pair), (
        "RST-2: the realistic verifier must confirm the placement"
    )
