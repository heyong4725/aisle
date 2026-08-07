"""Build the five-class VER-9 identity fixture from a recorded run.

`python tools/make_identity_fixtures.py --run runs/<id> [--out PATH]`

One rollout per med class (the rollout client cycles targets by episode
index, so `--episodes 5` covers all five) recorded with
`AISLE_FRAME_CAPTURE_PERIOD_S` set writes replayable frames; this picks,
per episode, the judged frame where the target scores highest (DELIVERED)
and the episode's first checkpoint (ABSENT, before the arm has moved
anything), crops both to the tray ROI, and stores them with that ROI.

Committing tray windows rather than whole frames keeps the fixture at
tens of KB while carrying exactly what the identity stage judges. CON-8:
JSON to stdout, logs to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "verifier" / "identity_classes.npz"


def _rows(traces: Path, topic: str) -> list[dict]:
    with pa.ipc.open_stream(traces / f"{topic}.arrow") as reader:
        return [row for batch in reader for row in batch.to_pylist()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    from aisle.scenes.pharmacy import load_meds, load_physics
    from aisle.verifier.models import detect_meds, load_pinned
    from aisle.verifier.oracle import build_judge_cfg
    from aisle.verifier.stages import crop_to_roi, detections_in_roi, tray_roi_pixels

    traces = args.run / "traces"
    calibration = json.loads(
        next(r["text"] for r in _rows(traces, "dora-genesis__bridge_info") if r["text"])
    )["calibration"]
    goals = [
        json.loads(r["text"]) for r in _rows(traces, "rollout-client__episode_goal") if r["text"]
    ]
    ends = [r["sim_time_ns"] for r in _rows(traces, "verifier-oracle__episode_result") if r["text"]]

    meds, physics = load_meds(), load_physics()
    cfg = build_judge_cfg(
        physics,
        meds,
        "franka",
        timeout_s=60.0,
        initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
        robot_home_error_rad=0.0,
    )
    roi = tray_roi_pixels(cfg.tray_min, cfg.tray_max, calibration, "overhead", None)
    pair = load_pinned("identity")
    frames = {int(p.stem): p for p in sorted((traces / "frames" / "overhead").glob("*.npz"))}

    arrays: dict[str, np.ndarray] = {"roi": np.asarray(roi, dtype=np.float64)}
    report = []
    low = 0
    for goal, high in zip(goals, ends, strict=True):
        target = goal["target_med"]
        window = [ns for ns in sorted(frames) if low < ns <= high]
        best_ns, best_score, offset = None, -1.0, None
        for ns in window:
            with np.load(frames[ns]) as data:
                crop, off = crop_to_roi(data["rgb"], roi)
            shifted = [
                {
                    **d,
                    "box": [
                        d["box"][0] + off[0],
                        d["box"][1] + off[1],
                        d["box"][2] + off[0],
                        d["box"][3] + off[1],
                    ],
                }
                for d in detect_meds(crop, list(meds), pair)
            ]
            score = detections_in_roi(shifted, roi, 0.0).get(target, 0.0)
            if score > best_score:
                best_ns, best_score, offset = ns, score, off
        for kind, ns in (("present", best_ns), ("absent", window[0])):
            with np.load(frames[ns]) as data:
                arrays[f"{target}_{kind}"] = crop_to_roi(data["rgb"], roi)[0]
        arrays["offset"] = np.asarray(offset, dtype=np.float64)
        report.append(
            {
                "med": target,
                "seed": goal["seed"],
                "present_sim_time_ns": best_ns,
                "present_score": round(best_score, 4),
                "absent_sim_time_ns": window[0],
            }
        )
        print(f"{target}: present @{best_ns / 1e9:.2f}s score {best_score:.4f}", file=sys.stderr)
        low = high

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **arrays)
    print(
        json.dumps(
            {"ok": True, "out": str(args.out), "bytes": args.out.stat().st_size, "episodes": report}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
