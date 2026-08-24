"""rollout-client node: episode driver for runnable graphs (SPEC 070).

Env-configured (the T09 rollout runner sets these):
  AISLE_SEEDS       comma-separated episode seeds       (default "0")
  AISLE_TIMEOUT_S   per-episode timeout                 (default 30)
  AISLE_RESULTS     JSONL output path                   (optional)
  AISLE_EPISODE_BASE run-global numbering offset on ADR-23 relaunches
                    (default 0; issue #160 item 5)

NOT runner-set: AISLE_TARGET_MEDS (comma-separated per-episode targets)
is SCRUBBED by the rollout runner, because nothing sets it there and no
graph declares it — an ambient shell value was the only way it could
reach a measured run, silently re-targeting every episode (PR #178
review). Absent it, targets are the deterministic seed-derived default
below. Graph tests that launch this node directly via `dora run` still
set it; they do not go through the runner.

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


def parse_episode_base(environ) -> int:
    """The ADR-23 run-global numbering offset, validated like every other
    env read in this module (PR #178 review).

    The runner always sets it, but a direct `dora run` from a developer
    shell does not, so a junk value must refuse LOUDLY at startup rather
    than die on an uncaught ValueError deep in the node. A NEGATIVE offset
    is refused too: it would mint ids like `ep--005` and alias earlier
    episodes, which is the exact aliasing the offset exists to prevent."""
    # isascii() guards the isdigit()/int() mismatch: str.isdigit() accepts
    # superscripts and other Unicode digit forms, so a bare isdigit() check
    # would let "²" through to an uncaught ValueError (the exact failure
    # this validation exists to prevent) and would silently read the
    # Arabic-Indic "٧" as 7 (PR #177 review).
    raw = environ.get("AISLE_EPISODE_BASE", "0").strip()
    if not (raw.isascii() and raw.isdigit()):
        raise SystemExit(
            f"rollout-client config refused: AISLE_EPISODE_BASE must be a "
            f"non-negative int, got {raw!r}"
        )
    return int(raw)


def main() -> None:
    import pyarrow as pa

    from aisle.scenes.pharmacy import MED_NAMES
    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    seeds = [int(s) for s in os.environ.get("AISLE_SEEDS", "0").split(",")]
    # parsed BEFORE the targets: the default target list is keyed to the
    # RUN-GLOBAL episode index, not this launch's local one (PR #177 review)
    episode_base = parse_episode_base(os.environ)
    tier = os.environ.get("AISLE_TIER", "T0")
    retail = tier in ("S1", "S2", "S3")  # RS-6: rollout gains --tier
    meds_env = os.environ.get("AISLE_TARGET_MEDS", "")
    inc2 = os.environ.get("AISLE_T4_INCREMENT_TWO", "").strip() in ("1", "true")
    if tier == "T4":
        # T4 (ADR-32 §1): the goal's target_med is the FINAL corrected
        # target — B on corrected seeds, else A — derived from the same
        # script the human-sim runs, so human and verifier can never
        # disagree. An AISLE_TARGET_MEDS override would desync the goal
        # from the script: refused, not ignored.
        # Increment two (ADR-32 §3 epoch): on corrected seeds goal 1 is
        # the MISDELIVERY (A — the human corrects only after receiving
        # it) and goal 2 is the recovery: deliver B with
        # expects_return=true (A sits in the tray at goal-2 start).
        from aisle.nodes.human_sim import final_target

        if meds_env:
            raise SystemExit(
                "rollout-client config refused: AISLE_TARGET_MEDS is incompatible "
                "with tier T4 — targets are script-derived (ADR-32)"
            )
        if inc2:
            from aisle.nodes.human_sim import is_corrected, requested_med

            targets = [requested_med(s) for s in seeds]  # goal 1 delivers A
        else:
            targets = [final_target(s) for s in seeds]
    elif tier == "T3" and not meds_env:
        # T3: the scene occludes med (seed % n) — the episode targets
        # exactly that med (aisle.scenes.pharmacy.occluded_target rule)
        targets = [MED_NAMES[s % len(MED_NAMES)] for s in seeds]
    else:
        targets = (
            meds_env.split(",")
            if meds_env
            # keyed to the RUN-GLOBAL episode index (PR #177 review): a
            # local index made a relaunch hand `ep-0002` a DIFFERENT med
            # than the clean run gave the same goal_id and seed — a CON-5
            # break that the run-global numbering made look contiguous and
            # correct. base is 0 on a first launch, so clean runs are
            # byte-identical.
            else [MED_NAMES[(episode_base + i) % len(MED_NAMES)] for i in range(len(seeds))]
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
    reset_mode_name = os.environ.get("AISLE_RESET_MODE", "teleport")
    if reset_mode_name not in ("teleport", "behavioral"):
        raise SystemExit(f"rollout-client config refused: unknown reset mode {reset_mode_name!r}")
    reset_mode = 1 if reset_mode_name == "behavioral" else 0
    results_path = os.environ.get("AISLE_RESULTS", "")
    lockstep = os.environ.get("AISLE_LOCKSTEP", "0").strip().lower() in ("1", "true", "yes")

    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)
    episode = 0  # index into THIS launch's seeds/targets
    # global numbering offset (issue #160 item 5): after a wall-clamp
    # relaunch (ADR-23) this client is the run's SECOND writer, and a
    # restart at ep-0000 would duplicate goal_ids — the VER-14 sidecar is
    # append-only and fidelity REFUSES duplicate goal_ids, so a relaunched
    # A7/both run would lose its whole VER-6 comparison. The runner passes
    # the count of episodes already recorded; goal_ids/request_ids/records
    # continue the run-global sequence
    phase = "reset_pending"  # -> awaiting_reset -> running -> (next)
    retries_seen: dict[str, int] = {}  # goal_id -> latest feedback retries (HAR-3)
    corrections_seen: dict[str, int] = {}  # goal_id -> dialogue_corrections (ADR-32)
    recovery_active = False  # T4 inc-2: one recovery goal per episode
    if inc2:
        from aisle.nodes.human_sim import is_corrected  # noqa: F401 — used in the loop
    # the sim stamp of the episode_result that ended the last episode: rides
    # every reset request (TC-2), so the realistic verifier can bound the
    # ended episode's frame window BEFORE any reset motion enters the scene
    # (issue #120) — under RST-2 behavioral resets the frames between the
    # result and reset_done show the med being picked back OUT of the tray
    last_result_sim_ns = 0
    # append, not truncate: after a wall-clamp relaunch (ADR-23) this
    # process is the SECOND writer to the same run's results file — a
    # truncate would erase the episodes and synthetic clamp records the
    # earlier launch already produced
    out = open(results_path, "a", buffering=1) if results_path else None

    for event in node:
        if event["type"] != "INPUT":
            continue
        if not env_accepts(event.get("metadata") or {}, env_pin):
            continue  # fleet mode (BRG-5): another env's stream
        if event["id"] in ("tick", "turn"):
            if (event["id"] == "tick" and lockstep) or (event["id"] == "turn" and not lockstep):
                continue
            if phase == "reset_pending" and episode < len(seeds):
                send(
                    "reset",
                    pa.array(np.array([seeds[episode], reset_mode], dtype=np.uint32)),
                    {
                        "request_id": f"reset-{episode_base + episode:04d}-{seeds[episode]}",
                        "sim_time_ns": last_result_sim_ns,
                    },
                )
                phase = "awaiting_reset"
                print(
                    f"reset sent: episode {episode_base + episode} seed {seeds[episode]}",
                    file=sys.stderr,
                )
        elif event["id"] == "reset_refused" and phase == "awaiting_reset":
            # ADR-34 (issue #195, issue #209): a refused reset ENDS the run.
            #
            # The client used to proceed on any reply, and the other
            # episode-state consumers stayed in phase with it by clearing on
            # the refusal too — they all received it, because it rode the
            # boundary topic. Once refusals reach only the requester, that
            # arrangement cannot survive: advancing here would put this node
            # in episode N+1 while ik-trajectory holds a stale plan, s1-expert
            # drops the new episode's plan as a duplicate, and nav carries a
            # leg across the boundary. That is issue #179's class, and the
            # comment deleted from budget_guard.py named the dependency.
            #
            # Stopping is also the honest answer on its own terms: the scene
            # was never reset, so the episode would measure nothing, and this
            # repo already chose refuse-over-degrade for `--reset behavioral`
            # (#196/#204). Exiting the loop is the client's NORMAL
            # termination path — results are flushed per line, so completed
            # episodes survive and the runner reports the short count.
            print(
                f"reset REFUSED ({(event.get('metadata') or {}).get('error')}) — ending the "
                f"run at episode {episode_base + episode}; the scene was never reset, and "
                "advancing would leave the rest of the graph an episode behind",
                file=sys.stderr,
            )
            # ADR-30: same rule as the normal termination below — a bare
            # `break` from inside the yielded turn raises GeneratorExit at
            # the yield, so `turn_node` never emits `turn_done` and the
            # terminal barrier blocks every other node until the ADR-23 wall
            # clamp. Ending the run must not become HANGING the run, which
            # is what issue #206 made reachable: refusals were unreachable
            # from the shipped client until a cold model cache could produce
            # one (cross-review of #223).
            phase = "done"
            if lockstep:
                node.stop_after_turn()
            else:
                break
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
            goal_id = f"ep-{episode_base + episode:04d}"
            send("episode_goal", pa.array([json.dumps(goal)]), {"goal_id": goal_id})
            if tier == "T4":
                # ADR-32 §2: the human-sim's script context — goal_id and
                # seed only, a FORWARD edge carrying no target information
                # beyond what the script derives
                meta = {"goal_id": goal_id, "seed": seeds[episode]}
                if inc2:
                    # inc-2 goal 1 IS the misdelivery: the human insists
                    # on A and the correction arrives POST-delivery (the
                    # recovery meta) — without this flag the inc-1 script
                    # corrected mid-dialogue against an A-goal (measured:
                    # seeds 0/4 goal-1 collisions, analysis/t4_inc2)
                    meta["inc2_goal1"] = True
                send(
                    "episode_meta",
                    pa.array([json.dumps(meta)]),
                    {"goal_id": goal_id},
                )
            phase = "running"
            print(
                f"goal sent: ep-{episode_base + episode:04d} {goal.get('target_med', '')}",
                file=sys.stderr,
            )
        elif event["id"] == "episode_feedback":
            # HAR-3: the retry count rides in the state machine's
            # feedback; the LATEST value per goal is what the episode
            # record carries (pass@8 is in-context retries, never
            # best-of-8 independent episodes)
            feedback = json.loads(event["value"][0].as_py())
            goal_id = (event.get("metadata") or {}).get("goal_id", "")
            if "retries" in feedback:
                retries_seen[goal_id] = int(feedback["retries"])
            if "dialogue_corrections" in feedback:
                # ADR-32 §2: corrections are their own counter, never
                # HAR-3 retries — the record carries both
                corrections_seen[goal_id] = int(feedback["dialogue_corrections"])
        elif event["id"] == "episode_result" and phase == "running":
            result = json.loads(event["value"][0].as_py())
            try:
                last_result_sim_ns = int((event.get("metadata") or {}).get("sim_time_ns", 0))
            except (TypeError, ValueError):
                # a malformed stamp degrades the reset bound, it must not
                # kill the client mid-campaign (PR #168 review)
                last_result_sim_ns = 0
            record = {"episode": episode_base + episode, "seed": seeds[episode], **result}
            if str(record.get("goal_id", "")).endswith("r"):
                record["recovery"] = True  # excluded from the runner's episode count
            record["retries"] = retries_seen.pop(record.get("goal_id", ""), 0)
            if tier == "T4":
                record["dialogue_corrections"] = corrections_seen.pop(record.get("goal_id", ""), 0)
            print(f"episode {episode_base + episode} result: {record}", file=sys.stderr)
            if out:
                out.write(json.dumps(record) + "\n")
            # T4 inc-2 (ADR-32 §3): on a corrected seed, goal 1's result
            # (A delivered) opens the RECOVERY goal — deliver B with
            # expects_return, NO reset in between (the tray still holds
            # A; that is the task). One recovery per episode.
            if inc2 and not recovery_active and is_corrected(seeds[episode]):
                from aisle.nodes.human_sim import corrected_med

                recovery_active = True
                goal_id = f"ep-{episode_base + episode:04d}r"
                goal = {
                    "target_med": corrected_med(seeds[episode]),
                    "tier": tier,
                    "timeout_s": timeout_s,
                    "seed": seeds[episode],
                    "expects_return": True,
                    "reset_sim_ns": last_result_sim_ns,
                }
                send("episode_goal", pa.array([json.dumps(goal)]), {"goal_id": goal_id})
                send(
                    "episode_meta",
                    pa.array(
                        [json.dumps({"goal_id": goal_id, "seed": seeds[episode], "recovery": True})]
                    ),
                    {"goal_id": goal_id},
                )
                print(f"recovery goal sent: {goal_id} {goal['target_med']}", file=sys.stderr)
                continue  # stay in `running` for the recovery verdict
            recovery_active = False
            episode += 1
            phase = "reset_pending"
            if episode >= len(seeds):
                print(f"all {len(seeds)} episodes done", file=sys.stderr)
                # cleanup reset: clears every node's episode state (plans,
                # targets, guard timers) so the idle graph stops moving
                send(
                    "reset",
                    pa.array(np.array([seeds[0], 0], dtype=np.uint32)),  # cleanup teleports
                    {"request_id": "reset-cleanup", "sim_time_ns": last_result_sim_ns},
                )
                phase = "done"
                # Do not `break` from inside the yielded turn: the wrapper
                # must first emit turn_done or the terminal barrier hangs.
                # It closes this turn and then ends iteration naturally.
                if lockstep:
                    node.stop_after_turn()
                else:
                    break


if __name__ == "__main__":
    main()
