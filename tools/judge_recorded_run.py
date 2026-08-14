"""Judge a RECORDED run with the realistic verifier, offline (VER-5/VER-6).

`python tools/judge_recorded_run.py --run runs/<id>`

Reads a run recorded with `AISLE_FRAME_CAPTURE_PERIOD_S` set (HAR-4,
ADR-11 clause 14) and replays every episode through
`verifier.realistic.judge_frames`, appending the VER-14 sidecar to the run
directory. `harness/fidelity.py --run-dir <id>` then produces the VER-6
agreement number against the oracle's own `episode_result`s.

This is the OFFLINE half of realistic-verifier increment 1: the dora node
that judges live and publishes `episode_result` with `verifier:"realistic"`
is increment 1b, and it will call the same `judge_frames`. Doing it offline
first means the fidelity number does not wait on the node, and it is
replayable — the frames are bytes on disk, so the same run can be re-judged
after any verifier change (VER-7).

Per episode it supplies what `judge_frames` needs from the traces: the
published calibration (BRG-8), the goal's target med, the frame window
between goal receipt and episode result, EE poses from FK of `joint_state`
for wrist frames (VER-8), and the terminal `joint_state` for VER-12.

CON-8: JSON to stdout, progress to stderr, exit 0 iff every episode was
judged.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

import numpy as np
import pyarrow as pa

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rows(traces: Path, topic: str) -> list[dict]:
    with pa.ipc.open_stream(traces / f"{topic}.arrow") as reader:
        return [row for batch in reader for row in batch.to_pylist()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from aisle.harness.trace_recorder import load_frames
        from aisle.nodes.budget_guard import fk_flange
        from aisle.scenes.pharmacy import load_meds, load_physics, wrist_mount_rotation
        from aisle.verifier.calibration import build_calibration_v1, quat_xyzw_from_rotation
        from aisle.verifier.oracle import build_judge_cfg
        from aisle.verifier.realistic import judge_frames

        traces = args.run / "traces"
        published = json.loads(
            next(r["text"] for r in _rows(traces, "dora-genesis__bridge_info") if r["text"])
        )["calibration"]
        goals = [
            json.loads(r["text"])
            for r in _rows(traces, "rollout-client__episode_goal")
            if r["text"]
        ]
        ends = [
            (r["sim_time_ns"], json.loads(r["text"]))
            for r in _rows(traces, "verifier-oracle__episode_result")
            if r["text"]
        ]
        joints = [
            (r["sim_time_ns"], np.asarray(r["data"], dtype=float))
            for r in _rows(traces, "dora-genesis__joint_state")
            if r["data"]
        ]
        # each episode's frames start STRICTLY AFTER its reset completes.
        # The frame AT the reset stamp still shows the PREVIOUS episode's
        # scene -- the render happens before the teleport is applied -- so
        # including it makes the previous delivery a wrong object in this
        # episode's tray and latches VER-9 on frame one. Windowing from the
        # previous episode_result (which is what this tool did first) is
        # exactly that bug: it cost a 0.20 fidelity number and an issue
        # blaming the detector for a correct detection.
        # the reset SERVICE's endpoint, not the bridge's (issue #192): a
        # successful BEHAVIORAL reset never reaches the bridge, so keying
        # off dora-genesis segments such a run by its fallbacks alone. The
        # bridge stays the fallback for runs with no service node. A
        # payload of 0 is a REFUSED reset (ADR-8), not a boundary — kept as
        # a legacy-compat guard for pre-ADR-34 recordings; refusals ride
        # `reset_refused` now and can no longer land here (issue #195).
        reset_rows: list[dict] = []
        for producer in ("reset", "dora-genesis"):
            reset_rows = _rows(traces, f"{producer}__reset_done")
            if reset_rows:
                break
        resets = sorted(
            r["sim_time_ns"] for r in reset_rows if r["data"] and float(r["data"][0]) != 0.0
        )

        meds, physics = load_meds(), load_physics()
        cam_cfg = physics["cameras"]
        # the NOMINAL block comes from the frozen config, never from the
        # published one: stage 0's whole job is to catch a published block
        # that drifted from what the config says (VER-8)
        nominal = build_calibration_v1(
            cam_cfg["overhead_pos"],
            cam_cfg["overhead_lookat"],
            published["overhead"]["resolution"],
            published["overhead"]["fov_deg"],
            cam_cfg["wrist_offset_m"],
            published["wrist"]["resolution"],
            published["wrist"]["fov_deg"],
            wrist_mount_rotation(cam_cfg),
        )
        nominal["_overhead_lookat"] = cam_cfg["overhead_lookat"]

        cfg = build_judge_cfg(
            physics,
            meds,
            "franka",
            timeout_s=60.0,
            initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
            robot_home_error_rad=0.0,
        )
        home = np.asarray(physics["embodiment"]["franka"]["home_qpos"], dtype=float)
        with open(REPO_ROOT / "src" / "aisle" / "verifier" / "thresholds.toml", "rb") as f:
            thresholds = tomllib.load(f)
        frames = load_frames(traces)
        if not frames:
            raise RuntimeError(
                f"{args.run} has no captured frames — re-record with "
                "AISLE_FRAME_CAPTURE_PERIOD_S set (ADR-11 clause 14)"
            )

        judged = []
        for goal, (end_ns, result) in zip(goals, ends, strict=True):
            before = [r for r in resets if r < end_ns]
            if not before:
                raise RuntimeError(f"no reset_done precedes {result['goal_id']}")
            low = before[-1]
            window = {
                camera: {s: f for s, f in per.items() if low < s <= end_ns}
                for camera, per in frames.items()
            }
            ee_poses = {}
            for stamp in window.get("wrist", {}):
                earlier = [q for s, q in joints if s <= stamp]
                if earlier:
                    pos, rot = fk_flange(earlier[-1][:7])
                    ee_poses[stamp] = (pos, quat_xyzw_from_rotation(rot))
            terminal_q = [q for s, q in joints if s <= end_ns]
            success, record = judge_frames(
                goal_id=result["goal_id"],
                target_med=goal["target_med"],
                med_names=list(meds),
                med_sizes={name: spec["size"] for name, spec in meds.items()},
                frames=window,
                calibration=published,
                nominal_calibration=nominal,
                jitter_bound_m=physics["domain_randomization"]["camera_jitter_m"],
                tray_min=cfg.tray_min,
                tray_max=cfg.tray_max,
                joint_state=terminal_q[-1] if terminal_q else home,
                home_qpos=home,
                thresholds=thresholds,
                ee_poses=ee_poses,
                run_dir=args.run,
            )
            stages = {k: v["vote"] for k, v in record["stages"].items()}
            judged.append(
                {
                    "goal_id": result["goal_id"],
                    "target": goal["target_med"],
                    "oracle": result["status"],
                    "realistic_success": success,
                    "stages": stages,
                }
            )
            print(
                f"{result['goal_id']} {goal['target_med']:12s} oracle={result['status']:8s} "
                f"realistic={'success' if success else 'fail'}  "
                f"{ {k: v for k, v in stages.items() if v != 'pass'} }",
                file=sys.stderr,
            )
            low = end_ns
    except Exception as exc:  # noqa: BLE001 — CON-8: report, never traceback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    # which stage blocks agreement most often — the VER-14/D4 attribution
    blocking: dict[str, int] = {}
    for row in judged:
        for stage, vote in row["stages"].items():
            if vote != "pass":
                blocking[stage] = blocking.get(stage, 0) + 1
    print(
        json.dumps(
            {
                "ok": True,
                "run": str(args.run),
                "episodes_judged": len(judged),
                "oracle_success": sum(1 for r in judged if r["oracle"] == "success"),
                "realistic_success": sum(1 for r in judged if r["realistic_success"]),
                "blocking_stages": dict(sorted(blocking.items(), key=lambda kv: -kv[1])),
                "episodes": judged,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
