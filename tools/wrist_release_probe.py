"""Does a WRIST frame exist that both sees the tray and shows the med
delivered? (issue #107, option 3.)

VER-9 judges the wrist at fixed `checkpoint_period_s` checkpoints plus the
terminal frame, and at that cadence the tray is out of the wrist's view at
every terminal frame — the arm has retreated by `episode_result`. Option 3
proposes re-selecting the wrist's judged frames around the release moment
instead. That is only worth specifying if such a frame EXISTS, which is
what this measures.

Reads a run recorded at a fine capture cadence and, for every wrist frame,
reports three independent facts:
  * tray in view    — the projected tray ROI overlaps the wrist image
                      (FK from joint_state at that frame's stamp, VER-8)
  * med delivered   — oracle_state puts the target inside the tray volume
  * target score    — the identity path run on the wrist window

The frames where the first two are BOTH true are option 3's candidates;
their scores say whether a usable operating point exists there.

CON-8: JSON to stdout, progress to stderr, **exit 0 iff ok** — and `ok`
is the question this tool asks: does at least one wrist frame see the tray
with the med delivered AND score at or above the identity threshold? A
non-zero exit carrying an `error` key is a tool failure; a non-zero exit
without one is the honest answer "no such frame exists".
Usage: uv run python tools/wrist_release_probe.py --run runs/<id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rows(traces: Path, topic: str) -> list[dict]:
    with pa.ipc.open_stream(traces / f"{topic}.arrow") as reader:
        return [row for batch in reader for row in batch.to_pylist()]


def _mat_to_quat_xyzw(rotation: np.ndarray) -> np.ndarray:
    w = np.sqrt(max(0.0, 1.0 + rotation[0, 0] + rotation[1, 1] + rotation[2, 2])) / 2
    if w < 1e-8:
        return np.array([0.0, 0.0, 0.0, 1.0])
    return np.array(
        [
            (rotation[2, 1] - rotation[1, 2]) / (4 * w),
            (rotation[0, 2] - rotation[2, 0]) / (4 * w),
            (rotation[1, 0] - rotation[0, 1]) / (4 * w),
            w,
        ]
    )


def target_delivered(state: np.ndarray, target_idx: int, cfg) -> bool:
    """Is the target RESTING in the tray at this frame?

    Deliberately the ORACLE's own predicate (`_aabb_inside_tray`), not a
    re-implementation: it is rotation-aware, bounds the footprint on both
    sides, and requires the box to rest on the tray floor. A hand-rolled
    "centre inside x/y and z below a ceiling" test counts an airborne box
    still in the gripper as delivered, which would let this tool certify
    a wrist operating point that does not exist (PR #104 review round 4).
    """
    from aisle.verifier.oracle import _aabb_inside_tray

    pos = np.asarray(state[target_idx * 7 : target_idx * 7 + 3], dtype=np.float64)
    quat_xyzw = np.asarray(state[target_idx * 7 + 3 : target_idx * 7 + 7], dtype=np.float64)
    return bool(_aabb_inside_tray(pos, cfg.box_half_extents[target_idx], quat_xyzw, cfg))


def vote_passes(scores: dict, target: str, min_score: float) -> bool:
    """VER-9's camera vote: the TARGET detects at or above threshold AND
    no non-target does. A frame carrying a surviving wrong class sets the
    episode latch, so it is not a passing candidate however well the
    target scored."""
    above = {label for label, score in scores.items() if score >= min_score}
    return target in above and above == {target}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from aisle.nodes.budget_guard import fk_flange
        from aisle.scenes.pharmacy import MED_NAMES, load_meds, load_physics
        from aisle.verifier.models import detect_meds, load_pinned
        from aisle.verifier.oracle import build_judge_cfg, load_thresholds
        from aisle.verifier.stages import (
            crop_to_roi,
            detections_in_roi,
            med_box_area_limit,
            shift_detections,
            tray_roi_pixels,
        )

        traces = args.run / "traces"
        calibration = json.loads(
            next(r["text"] for r in _rows(traces, "dora-genesis__bridge_info") if r["text"])
        )["calibration"]
        goals = [
            json.loads(r["text"])
            for r in _rows(traces, "rollout-client__episode_goal")
            if r["text"]
        ]
        ends = [
            r["sim_time_ns"] for r in _rows(traces, "verifier-oracle__episode_result") if r["text"]
        ]
        joints = [
            (r["sim_time_ns"], np.asarray(r["data"], dtype=float))
            for r in _rows(traces, "dora-genesis__joint_state")
            if r["data"]
        ]
        oracle = [
            (r["sim_time_ns"], np.asarray(r["data"], dtype=float))
            for r in _rows(traces, "dora-genesis__oracle_state")
            if r["data"]
        ]

        meds, physics = load_meds(), load_physics()
        thresholds = load_thresholds()["realistic"]
        cfg = build_judge_cfg(
            physics,
            meds,
            "franka",
            timeout_s=60.0,
            initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
            robot_home_error_rad=0.0,
        )
        med_sizes = {name: spec["size"] for name, spec in meds.items()}
        pair = load_pinned("identity")
        wrist_dir = traces / "frames" / "wrist"
        stamps = sorted(int(p.stem) for p in wrist_dir.glob("*.npz"))

        def ee_at(ns: int):
            earlier = [q for s, q in joints if s <= ns]
            if not earlier:
                return None
            pos, rot = fk_flange(earlier[-1][:7])
            return (pos, _mat_to_quat_xyzw(rot))

        def delivered_at(ns: int, target: str) -> bool:
            earlier = [state for s, state in oracle if s <= ns]
            if not earlier:
                return False
            return target_delivered(earlier[-1], MED_NAMES.index(target), cfg)

        rows_out = []
        low = 0
        for goal, high in zip(goals, ends, strict=True):
            target = goal["target_med"]
            for ns in [s for s in stamps if low < s <= high]:
                ee = ee_at(ns)
                in_view, score, scores = False, None, {}
                if ee is not None:
                    roi = tray_roi_pixels(cfg.tray_min, cfg.tray_max, calibration, "wrist", ee)
                    window = None
                    if roi is not None:
                        with np.load(wrist_dir / f"{ns:020d}.npz") as data:
                            window = crop_to_roi(data["rgb"], roi)
                    if window is not None:
                        in_view = True
                        limit = med_box_area_limit(
                            cfg.tray_min,
                            cfg.tray_max,
                            med_sizes,
                            calibration,
                            thresholds["identity_max_box_area_slack"],
                            "wrist",
                            ee,
                        )
                        scores = detections_in_roi(
                            shift_detections(detect_meds(window[0], MED_NAMES, pair), window[1]),
                            roi,
                            0.0,
                            limit,
                        )
                        scores = {k: round(float(v), 4) for k, v in scores.items()}
                        score = scores.get(target, 0.0)
                rows_out.append(
                    {
                        "sim_time_ns": ns,
                        "target": target,
                        "tray_in_view": in_view,
                        "delivered": delivered_at(ns, target),
                        "target_score": score,
                        "scores": scores,
                        "wrong_object": sorted(
                            label
                            for label, value in scores.items()
                            if label != target and value >= thresholds["identity_min_score"]
                        ),
                    }
                )
            low = high
    except Exception as exc:  # noqa: BLE001 — CON-8: report, never traceback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    candidates = [r for r in rows_out if r["tray_in_view"] and r["delivered"]]
    passing = [
        r
        for r in candidates
        if vote_passes(r["scores"], r["target"], thresholds["identity_min_score"])
    ]
    report = {
        "ok": bool(passing),
        "wrist_frames": len(rows_out),
        "tray_in_view": sum(1 for r in rows_out if r["tray_in_view"]),
        "delivered": sum(1 for r in rows_out if r["delivered"]),
        "candidates_in_view_and_delivered": len(candidates),
        "candidates_passing_ver9_vote": len(passing),
        "candidates_with_wrong_object": sum(1 for r in candidates if r["wrong_object"]),
        "best_candidate_score": max((r["target_score"] or 0.0 for r in candidates), default=None),
        "threshold": thresholds["identity_min_score"],
    }
    if args.out:
        args.out.write_text(json.dumps({**report, "rows": rows_out}, indent=2))
    for r in candidates[:40]:
        print(
            f"  candidate @{r['sim_time_ns'] / 1e9:6.2f}s {r['target']:12s} "
            f"score={r['target_score']}",
            file=sys.stderr,
        )
    print(json.dumps(report))
    if not report["ok"]:
        print(
            f"no wrist frame yields a VER-9 vote: {len(candidates)} frame(s) saw the tray "
            f"with the med delivered, best target score {report['best_candidate_score']} "
            f"against a {report['threshold']} threshold, "
            f"{report['candidates_with_wrong_object']} carrying a surviving non-target",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
