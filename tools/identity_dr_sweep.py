"""Sweep the VER-9 identity operating point across SCN-6 domain
randomization: every med class, several seeds, and the DR axis
combinations the scene supports.

For each cell it builds the scene, teleports ONLY the target upright into
the tray centre, settles, renders the overhead camera, and runs the exact
production identity path (`tray_roi_pixels` -> `crop_to_roi` ->
`detect_meds` -> `shift_detections` -> `med_box_area_limit` ->
`detections_in_roi`) against the scene's REALIZED calibration.

Reports, per cell, whether the target survives and whether any non-target
does — the latter is the wrong-object latch firing on a correct delivery.

CON-8: JSON to stdout, progress to stderr, **exit 0 iff ok** — and `ok` is
the MEASURED property, not "the script ran": every cell must produce a
usable VER-9 vote, i.e. the delivered target detects AND no non-target
survives. A miss-only sweep is not a successful calibration. A non-zero
exit with an `error` key is a tool failure; a non-zero exit without one
means the sweep found misses, false latches, or both.

Usage: uv run python tools/identity_dr_sweep.py [--seeds 3,9,11] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]

# every SCN-6 combination of the three axes that affect the identity
# signal, so the envelope can be an ALLOWLIST of measured configurations
# rather than a semantic phrase a reader has to interpret (#111 review)
AXES = (
    ("nominal", ()),
    ("lighting", ("lighting",)),
    ("textures", ("textures",)),
    ("camera_jitter", ("camera_jitter",)),
    ("lighting+textures", ("lighting", "textures")),
    ("lighting+camera_jitter", ("lighting", "camera_jitter")),
    ("textures+camera_jitter", ("textures", "camera_jitter")),
    ("all", ("lighting", "textures", "camera_jitter")),
)


def sweep_ok(results: list[dict]) -> bool:
    """A VER-9 camera vote needs BOTH halves: the target detects, and no
    non-target survives to set the latch. So a sweep in which every
    delivery is missed is not a usable calibration even though nothing
    latched — `ok` requires no misses AND no latches (PR #104 review
    round 4)."""
    return all(r["detected"] and not r["wrong_object"] for r in results)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="3,9,11")
    parser.add_argument(
        "--configs",
        default=None,
        help="comma-separated DR configuration names to run (default: all)",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    try:
        seeds = [int(s) for s in args.seeds.split(",")]
    except ValueError as exc:  # CON-8: refuse with JSON, never a traceback
        print(json.dumps({"ok": False, "error": f"--seeds must be integers: {exc}"}))
        return 1
    wanted = set(args.configs.split(",")) if args.configs else None
    axes = [a for a in AXES if wanted is None or a[0] in wanted]
    if not axes:
        print(json.dumps({"ok": False, "error": f"no DR configuration matches {args.configs!r}"}))
        return 1

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from aisle.nodes.dora_genesis import realized_calibration
    from aisle.scenes.pharmacy import (
        MED_NAMES,
        DRToggle,
        SceneCfg,
        build_scene,
        load_meds,
        load_physics,
        resolve_layout,
        to_numpy,
    )
    from aisle.verifier.models import detect_meds, load_pinned
    from aisle.verifier.oracle import build_judge_cfg, load_thresholds
    from aisle.verifier.stages import (
        crop_to_roi,
        detections_in_roi,
        med_box_area_limit,
        shift_detections,
        tray_roi_pixels,
    )

    try:
        meds, physics = load_meds(), load_physics()
        layout = resolve_layout(physics, "franka")
        tray = layout["tray"]
        tray_top = tray["pos"][2] + tray["size"][2] / 2
        thresholds = load_thresholds()["realistic"]
        cfg_judge = build_judge_cfg(
            physics,
            meds,
            "franka",
            timeout_s=60.0,
            initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
            robot_home_error_rad=0.0,
        )
        med_sizes = {name: spec["size"] for name, spec in meds.items()}
        pair = load_pinned("identity")

        results = []
        for seed in seeds:
            for axis_name, axis_set in axes:
                toggles = {a: DRToggle(enabled=True, seed=seed) for a in axis_set}
                handle = build_scene(
                    seed=seed, embodiment="franka", n_envs=1, headless=True, cfg=SceneCfg(**toggles)
                )
                calibration = realized_calibration(handle, physics, is_store=False)
                roi = tray_roi_pixels(
                    cfg_judge.tray_min, cfg_judge.tray_max, calibration, "overhead"
                )
                limit = med_box_area_limit(
                    cfg_judge.tray_min,
                    cfg_judge.tray_max,
                    med_sizes,
                    calibration,
                    thresholds["identity_max_box_area_slack"],
                    "overhead",
                )
                for target in MED_NAMES:
                    box = handle.boxes[target]
                    home = to_numpy(box.get_pos()).reshape(-1)[:3].copy()
                    box.set_pos(
                        np.array(
                            [tray["pos"][0], tray["pos"][1], tray_top + 0.05], dtype=np.float32
                        )
                    )
                    box.set_quat(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
                    for _ in range(60):
                        handle.scene.step()
                    rgb = np.asarray(handle.cams["overhead"].render(rgb=True)[0], dtype=np.uint8)
                    window = crop_to_roi(rgb, roi)
                    scores = {}
                    if window is not None:
                        scores = detections_in_roi(
                            shift_detections(detect_meds(window[0], MED_NAMES, pair), window[1]),
                            roi,
                            thresholds["identity_min_score"],
                            limit,
                        )
                    wrong = sorted(k for k in scores if k != target)
                    results.append(
                        {
                            "seed": seed,
                            "dr": axis_name,
                            "target": target,
                            "target_score": round(float(scores.get(target, 0.0)), 4),
                            "detected": target in scores,
                            "wrong_object": wrong,
                            "roi": [round(float(v), 1) for v in roi],
                            "gate_px2": round(float(limit), 1),
                        }
                    )
                    print(
                        f"seed {seed:3d} {axis_name:18s} {target:12s} "
                        f"target={scores.get(target, 0.0):.4f} wrong={wrong}",
                        file=sys.stderr,
                    )
                    box.set_pos(home)
                    box.set_quat(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
                    for _ in range(30):
                        handle.scene.step()
                del handle
    except Exception as exc:  # noqa: BLE001 — CON-8: report, never traceback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    missed = [r for r in results if not r["detected"]]
    latched = [r for r in results if r["wrong_object"]]
    report = {
        "ok": sweep_ok(results),
        "cells": len(results),
        "false_negatives": len(missed),
        "false_latches": len(latched),
        "latching_cells": latched[:20],
        "results": results,
    }
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "results"}))
    if not report["ok"]:
        print(
            f"{len(latched)} cell(s) latched a wrong object on a correct delivery, "
            f"{len(missed)} cell(s) missed the delivered target",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
