"""rollout-client node: episode driver for runnable graphs (SPEC 070).

Env-configured (the T09 rollout runner sets these):
  AISLE_SEEDS       comma-separated episode seeds       (default "0")
  AISLE_TARGET_MEDS comma-separated per-episode targets (default cycles meds)
  AISLE_TIMEOUT_S   per-episode timeout                 (default 30)
  AISLE_RESULTS     JSONL output path                   (optional)

Per episode: reset(seed) -> await reset_done -> episode_goal -> await
episode_result -> record, next seed. After the final episode the CLIENT
exits its event loop (results flushed per line, so a killed run keeps
completed episodes); tearing down the rest of the dataflow is the rollout
runner's job (T09) — the bridge never exits on its own. Env config is
validated at startup and refused loudly (a bad med name would otherwise
deadlock the run: the verifier refuses unknown goals without a result).
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np


def main() -> None:
    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import MED_NAMES
    from aisle.topics import make_sender

    seeds = [int(s) for s in os.environ.get("AISLE_SEEDS", "0").split(",")]
    tier = os.environ.get("AISLE_TIER", "T0")
    retail = tier in ("S1", "S2", "S3")  # RS-6: rollout gains --tier
    meds_env = os.environ.get("AISLE_TARGET_MEDS", "")
    targets = (
        meds_env.split(",")
        if meds_env
        else [MED_NAMES[i % len(MED_NAMES)] for i in range(len(seeds))]
    )
    # refuse bad config LOUDLY at startup: an unknown med deadlocks the run
    # (the verifier refuses the goal without emitting a result), and a
    # short target list would IndexError mid-run. Retail tiers carry no
    # target_med — their goals come from the seeded episode generator.
    if not retail:
        unknown = [m for m in targets if m not in MED_NAMES]
        if unknown or len(targets) != len(seeds):
            raise SystemExit(
                f"rollout-client config refused: unknown meds {unknown}, "
                f"{len(targets)} targets for {len(seeds)} seeds"
            )
    timeout_s = float(os.environ.get("AISLE_TIMEOUT_S", "30"))
    results_path = os.environ.get("AISLE_RESULTS", "")

    node = Node()
    send = make_sender(node)
    episode = 0
    phase = "reset_pending"  # -> awaiting_reset -> running -> (next)
    out = open(results_path, "w", buffering=1) if results_path else None

    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "tick":
            if phase == "reset_pending" and episode < len(seeds):
                send(
                    "reset",
                    pa.array(np.array([seeds[episode], 0], dtype=np.uint32)),
                    {"request_id": f"reset-{episode:04d}-{seeds[episode]}"},
                )
                phase = "awaiting_reset"
        elif event["id"] == "reset_done" and phase == "awaiting_reset":
            reset_meta = event.get("metadata") or {}
            if retail:
                # RS-3/RS-6: the retail goal IS the seeded oracle task
                # description; the verifier derives requirements from it
                from aisle.scenes.store import generate_episode

                goal = generate_episode(seeds[episode], tier)
            else:
                goal = {"target_med": targets[episode]}
            goal |= {
                "tier": tier,
                "timeout_s": timeout_s,
                "seed": seeds[episode],
                # the teleport's sim time (BRG-4): the verifier captures the
                # episode's initial poses only from an oracle sample at or
                # after this, so a pre-reset frame can never become the
                # baseline (else the teleport reads as a mass collision)
                "reset_sim_ns": int(reset_meta.get("sim_time_ns", 0)),
            }
            send(
                "episode_goal",
                pa.array([json.dumps(goal)]),
                {"goal_id": f"ep-{episode:04d}"},
            )
            phase = "running"
        elif event["id"] == "episode_result" and phase == "running":
            result = json.loads(event["value"][0].as_py())
            record = {"episode": episode, "seed": seeds[episode], **result}
            print(f"episode {episode} result: {record}", file=sys.stderr)
            if out:
                out.write(json.dumps(record) + "\n")
            episode += 1
            phase = "reset_pending"
            if episode >= len(seeds):
                print(f"all {len(seeds)} episodes done", file=sys.stderr)
                # cleanup reset: clears every node's episode state (plans,
                # targets, guard timers) so the idle graph stops moving
                send(
                    "reset",
                    pa.array(np.array([seeds[0], 0], dtype=np.uint32)),
                    {"request_id": "reset-cleanup"},
                )
                phase = "done"
                break  # client exits; dataflow teardown is the runner's job


if __name__ == "__main__":
    main()
