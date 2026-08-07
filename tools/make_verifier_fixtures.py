"""Render the golden frames the realistic verifier's tests judge (VER-7).

Class A tool: builds a desk scene at a fixed seed, teleports the target
box into the tray (so one frame is a genuine success and one is not),
and saves the camera payloads plus the calibration block to a small
.npz under tests/fixtures/verifier/.

The fixture is COMMITTED so the determinism replay (VER-7) and the
stage tests run from identical bytes on any machine — the models must
produce bit-identical outputs from bit-identical inputs, and a
re-rendered frame would confound a model change with a render change.

Usage: uv run python tools/make_verifier_fixtures.py [--out PATH]
CON-8: JSON to stdout, exit 0 iff ok.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "verifier" / "golden_frames.npz"
SEED = 3
# small on purpose: two judged moments are enough for VER-7 (the replay
# needs identical inputs, not many) and the fixture is committed
MOMENTS = ("before", "delivered")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from aisle.nodes.dora_genesis import realized_calibration
        from aisle.scenes.pharmacy import (
            MED_NAMES,
            build_scene,
            load_physics,
            oracle_state,
            resolve_layout,
        )

        physics = load_physics()
        layout = resolve_layout(physics, "franka")
        handle = build_scene(seed=SEED, embodiment="franka", n_envs=1, headless=True)
        calibration = realized_calibration(handle, physics, is_store=False)

        target_idx = 3
        target = handle.entities[target_idx]
        tray = layout["tray"]
        tray_top = tray["pos"][2] + tray["size"][2] / 2

        payload: dict[str, np.ndarray] = {}
        for moment in MOMENTS:
            if moment == "delivered":
                # teleport the target into the tray, upright, and settle
                pos = np.array([tray["pos"][0], tray["pos"][1], tray_top + 0.05], dtype=np.float32)
                target.set_pos(pos)
                target.set_quat(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
                for _ in range(60):
                    handle.scene.step()
            else:
                for _ in range(30):
                    handle.scene.step()
            rgb_o, depth_o, _, _ = handle.cams["overhead"].render(rgb=True, depth=True)
            rgb_w = handle.cams["wrist"].render()[0]
            payload[f"{moment}_rgb_overhead"] = np.asarray(rgb_o, dtype=np.uint8)
            payload[f"{moment}_depth_overhead"] = np.asarray(depth_o, dtype=np.float32)
            payload[f"{moment}_rgb_wrist"] = np.asarray(rgb_w, dtype=np.uint8)
            payload[f"{moment}_oracle_state"] = np.asarray(
                oracle_state(handle), dtype=np.float32
            ).reshape(-1)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            args.out,
            calibration=np.array(json.dumps(calibration)),
            med_names=np.array(MED_NAMES),
            target_med=np.array(MED_NAMES[target_idx]),
            seed=np.array(SEED),
            **payload,
        )
    except Exception as exc:  # noqa: BLE001 — CON-8: report, never traceback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(args.out),
                "bytes": args.out.stat().st_size,
                "moments": list(MOMENTS),
                "target_med": MED_NAMES[target_idx],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
