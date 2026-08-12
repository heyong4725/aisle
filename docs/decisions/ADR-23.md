# ADR-23 — Per-episode wall clamp in `harness rollout`

Status: accepted (CON-15). Trigger: H3 campaign 2, W/S2 holdout.

## Problem

`rollout` had two wall-time defenses: an overall run budget and a
trace-growth stall detector. Neither bounds a SINGLE episode. In the
W/S2 holdout, one episode ran ~4 hours — the graph's episode-end
condition never fired (episode timeout is in SIM seconds, and a slow
sim stretches it arbitrarily in wall time) while traces kept growing
(video encoder), so the stall detector stayed quiet and the wedged
episode consumed the entire scoring window. Five of eight held-out
seeds were never scored; the scenario's holdout cell is a partial.

## Decision

An episode gets the tier's per-episode WALL budget
(`tier_budgets(tier)[1]`, plus the Genesis build grace when it is the
first episode of a launch). Past that:

1. the graph is killed and orphans reaped;
2. a synthetic episode record is appended for the wedged seed:
   `status: fail, failure: wall_clamp, synthetic: true` — a recorded
   outcome, not an infra abort (same principle as the holdout-timeout
   decision in PR #49);
3. the run RELAUNCHES with the remaining seeds, so one wedged episode
   costs exactly its budget instead of every seed after it.

The manifest records `wall_clamped` (seeds) and `relaunches`.
`rollout_client` now opens the results file in append mode — after a
relaunch it is the second writer to the same file.

Hardened per the PR #58 review:

- **Reap before relaunch.** Stale nodes from the killed launch are
  concurrent writers to results/traces (dora-rs/dora#2856); orphans are
  reaped between terminate and respawn, not only in the `finally`.
- **Per-launch trace dirs.** The recorder truncates its Arrow/video
  files on open, so each relaunch gets its own instrumented graph
  (`graph-rN.yaml`) pointing at `traces/relaunch-N/` — the prior
  launch's evidence survives (HAR-4). The stall watcher and video
  listing scan recursively.
- **Deadline grows with relaunches.** Each relaunch pays a fresh
  Genesis build, so the deadline extends by the build grace per
  relaunch — still bounded by ADR-21's campaign wall cap — else
  consecutive wedges cut the tail seeds out of the original budget.

## Consequences

- `wall_clamp` joins the failure histogram as a harness-synthesized
  class, distinguishable from the verifier's sim-time `timeout`.
- Episode indices, reset request ids, and goal ids continue in run-global
  order across launches. Each relaunch receives the number of episode rows
  already recorded as its offset, so the append-only results and VER-14
  sidecar retain one unambiguous correlation key per attempted episode.
  Trace files remain isolated per launch under `traces/relaunch-N/`; the
  global ids identify their episode windows without aliasing prior launches.
- A pathological graph that wedges every episode now finishes in
  ~N x per-episode budget with N recorded `wall_clamp` failures — a
  scored 0.0, not an empty window.

IDs: HAR-1 (rollout semantics), CON-5 (records stay reproducible),
ADR-21 (budget interplay), PR #49 (timeout-as-outcome precedent).
