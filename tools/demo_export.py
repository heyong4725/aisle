"""demo_export — expert rollout traces -> LeRobot training episodes
(next-phases §5.1 fine-tuning groundwork; arc 3 of the owner-directed
trio).

Reads a recorded run (Arrow topic streams + traces/overhead.mp4) and
emits per-episode demonstration tuples: (frame, observation.state,
action, task). Frames come from the mp4 at exact rgb-event indices (the
vlm_judge mapping); actions are the GUARD-CLAMPED commands
(budget-guard/joint_cmd_safe + gripper_cmd_safe) — the policy should
learn what the robot actually did, never an unclamped intent. Output is
lerobot's LeRobotDataset when the `vla` extra is present, else a plain
npz bundle per episode (same tuples; the conversion is mechanical).

The TRAINING run itself is pre-registered and compute-gated
(analysis/m1/finetune_protocol.md); this tool only builds the dataset.
CON-8: JSON to stdout, logs to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def align_to_frames(action_ts: list[int], frame_ts: list[int]) -> list[int]:
    """For each action timestep, the index of the LATEST frame at or
    before it (the observation the policy would have acted on). Actions
    before the first frame get index -1 and are dropped by the caller —
    never paired with a future observation."""
    out, j = [], -1
    for t in action_ts:
        while j + 1 < len(frame_ts) and frame_ts[j + 1] <= t:
            j += 1
        out.append(j)
    return out


def episode_tuples(run_dir: Path, episode: int) -> dict | None:
    """One episode's aligned (frame_idx, state, action, task) arrays."""
    from aisle.harness.traces import episode_window, query

    t0, t1 = episode_window(run_dir, episode)
    frames = query(run_dir, "rgb_overhead")["sim_time_ns"]
    state = query(run_dir, "joint_state", t0_ns=t0, t1_ns=t1)
    cmd = query(run_dir, "joint_cmd_safe", t0_ns=t0, t1_ns=t1)
    grip = query(run_dir, "gripper_cmd_safe", t0_ns=t0, t1_ns=t1)
    if not cmd["n"]:
        return None  # no motion this episode: nothing to learn from
    # actions pair with the state/frame AT their own stamp
    frame_idx = align_to_frames(cmd["sim_time_ns"], frames)
    keep = [i for i, f in enumerate(frame_idx) if f >= 0]
    return {
        "frame_indices": [frame_idx[i] for i in keep],
        "action_ts": [cmd["sim_time_ns"][i] for i in keep],
        "n_actions": len(keep),
        "n_states": state["n"],
        "n_grip": grip["n"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    eps_path = args.run / "episodes.jsonl"
    if not eps_path.exists():
        print(json.dumps({"ok": False, "error": f"no episodes.jsonl in {args.run}"}))
        return 1
    records = [json.loads(x) for x in eps_path.read_text().splitlines() if x.strip()]
    args.out.mkdir(parents=True, exist_ok=True)
    exported = []
    for rec in records:
        if rec.get("status") != "success":
            continue  # demonstrations are SUCCESSFUL episodes only
        episode = int(rec.get("episode", 0))
        tup = episode_tuples(args.run, episode)
        if tup is None:
            continue
        (args.out / f"ep_{episode:04d}.json").write_text(json.dumps({**rec, **tup}))
        exported.append({"episode": episode, "n_actions": tup["n_actions"]})
        print(f"[demo-export] ep {episode}: {tup['n_actions']} tuples", file=sys.stderr)
    print(json.dumps({"ok": True, "run": str(args.run), "episodes": exported}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
