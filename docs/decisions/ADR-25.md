# ADR-25 — Reset-anchored startup: no physics step before the first reset (issue #71)

Status: PROPOSED (agent-drafted 2026-08-02, CON-15). Trigger: issue #71
(CON-5 violation — attested expert_s1 pair diverged on identical seeds).
Relates to SPEC 030 BRG-1/BRG-4, SPEC 010 TC-6, CON-5.

## Problem

Two attested `expert_s1` rollouts at ONE pin (identical `git_sha`,
`env_hash`, `graph_hash`, `env_fingerprint`, platform, seeds 100..107;
manifests differ only in `run_id`) produced different results: seeds 100
and 104 flipped `extra_item` -> `timeout`, seed 107 shifted `t_end`
294.0 -> 294.1 (`runs/s1-det-pair-{1,2}`).

Trace bisection found the first divergence at sim step 0: in pair-1 the
bridge serviced one `tick` (one physics step) before the first `reset`
request landed (`reset_done` at sim 0.01 s, `episode_goal.reset_sim_ns`
= 10000000); in pair-2 the reset landed first (sim 0.00 s / 0). BRG-1
services inputs in ARRIVAL order, and the arrival order of the first
timer tick vs. the first reset request is a wall-clock race.

The same traces prove post-reset physics is deterministic: pair-2's
`joint_state` stream equals pair-1's shifted by exactly one step
(pair-1 seq N+1 == pair-2 seq N, bit-identical), and `order` /
`subtask_plan` were identical across all eight episodes (no RNG
involvement — a code audit found no uninjected `random`/`np.random`/
wall-clock reads in mobility, nav, task-planner, expert, or client).
The divergence mechanism is downstream phase sensitivity: the expert's
first `joint_cmd` is computed from a `joint_state` that had one settle
step vs. zero, differs at the 1e-9 level, and chaos plus guard
interactions amplify from there (first nav 25.7 s vs 6.68 s).

## Decision

1. **The bridge MUST NOT step physics before the first reset.** Ticks
   arriving while no reset has ever been serviced are dropped: sim time
   stays 0, no tick-driven topic is published (the one-shot startup
   announcements bridge_info (BRG-6) and frame_info still fire), and
   the first `reset_done` therefore always carries `sim_time_ns == 0`.
   Episode 0 starts from the seed-injected state at sim step 0, exactly.
2. **Every reset re-anchors the publish-cadence grid.** The
   `RateScheduler` is re-instantiated on reset, so which post-reset
   ticks fire the sub-100 Hz topics (`poses`, `oracle_state`,
   `base_pose`, cameras) is a function of the episode, not of the wall
   tick the request happened to land on. The determinism claim is
   scoped to PAYLOADS and reset-relative sim stamps: `seq` counters are
   run-global and absolute sim time still depends on when earlier
   episodes ended.
3. **Bring-up escape hatch, attested:** `AISLE_STEP_WITHOUT_RESET=1`
   restores the old free-running behavior for reset-less debug graphs
   (conformance/multi-env/mobile-integration/contract-acceptance
   tests). Two guards keep it out of measured runs: the flag is
   surfaced in `bridge_info` (recorded in every run's traces, so a
   free-running bridge is auditable), and the rollout runner SCRUBS the
   variable from the dora process environment (`scrub_bringup_env`,
   HAR-1) — ambient shell state cannot silently flip a measured run.
   Setting it via graph YAML changes the graph hash, which the manifest
   records.

Interpretation recorded per CON-15: BRG-1's "each tick advances sim by
cfg.dt" is read as applying to the seeded world — the world does not
exist for stepping purposes until the first seed injection. No spec text
is edited by this ADR.

## What this does NOT fix (residual, tracked in issue #71)

- **Pipeline wall latency quantizes into sim ticks — on EVERY tier.**
  Observed while validating this ADR (M0 acceptance pair
  `m0-{1,2}` on a heavily loaded machine): both runs started episode 0
  from the identical seed-injected state at sim 0 (this ADR's gate,
  working), all seed-derived plans bit-identical — yet the expert's
  FIRST joint_cmd reached the bridge at sim 0.61 s in one run and
  0.79 s in the other, because the sim keeps stepping on its wall
  timer while the oracle-pose -> grasp-planner -> ik-trajectory chain
  computes. Under load that latency swung ~180 ms (18 ticks) and seed
  0 flipped success -> timeout: SPEC 090's M0-1 (pass1 >= 0.95) and
  M0-2 (replicate) are LOAD-SENSITIVE, and the original 50/50
  replicate passed on an idle machine, not by construction. The
  structural fix is sim-clock lockstep (the bridge waits for the
  command chain each tick, or the chain is triggered per sim step) — a
  SPEC 030/010 contract change needing human review.

- **Wall-clock control loops** (S1-only): `waypoint-nav` ticks at 20 ms
  wall and `budget-guard`'s `base_watchdog` at 50 ms wall. Command
  VALUES are pose-determined, but whether a recomputed `base_cmd` lands
  before the bridge's next tick is a wall race. Retiming them onto sim
  topics requires editing `graphs/expert_s1.yaml` and `budget_guard.py`
  — both in the CON-7 frozen set: needs human review.
- **The Metal GPU backend is not bit-deterministic — bit-exact CON-5 is
  unattainable on it.** MEASURED while iterating this ADR's acceptance
  test (identical seed, identical startup, gate verified working):
  occasional single-ULP `joint_state` flips between two cold runs, at
  unpredictable steps — one pair diverged one ULP at step 7 of episode
  0 (joint 0: -1.5411730203e-7 vs -1.5411731624e-7 rad), another pair
  ran episode 0 bit-identical and flipped one ULP 74 steps after the
  second reset, a third ran 4 s bit-identical. GPU parallel-reduction
  ordering is the standard cause. Consequences: (a) chaos amplifies ULP
  noise over a 600-sim-s episode, so even single-episode-per-launch
  pairs are independent SAMPLES, not replicas — the S1 pair's seed-107
  294.0-vs-294.1 drift is this noise; (b) CON-5's "same seed ⇒ same
  result" needs a human decision: interpret attested comparisons
  statistically (distribution over seeds, not per-seed equality), or
  run attested pairs on the Genesis CPU backend if it proves
  bit-deterministic (perf cost unmeasured). The acceptance test
  accordingly pins bit-hard only what IS exact — first reset at sim 0,
  no pre-reset physics, identical cadence stamp grids — and compares
  state values with a 1e-6 ULP-noise tolerance.
- **Inter-episode reset arrival**: the tick at which episode i+1's reset
  lands depends on wall latency of the verifier -> rollout-client ->
  reset chain (the sim keeps stepping during the round trip). Decision 2
  removes the cadence-phase residue of that race.
- **A refused/lost first reset now freezes the sim instead of timing
  out**: the rollout client accepts any `reset_done` (even an error
  reply) and the verifier's episode timeout counts SIM seconds, which
  no longer advance pre-reset — a malformed first reset would stall the
  dataflow until the wall-clamp relaunch instead of failing fast. The
  production client always sends well-formed resets, so this is a
  robustness gap, not a live bug; fix candidate: the client should
  treat an error `reset_done` as a failed episode. Same trap applies to
  a hypothetical mid-run hot-swap of the bridge itself (the swapped-in
  bridge would await a first reset nobody re-sends).
- H4's relaunch-latency table on main (PR #79) was measured on the
  pre-ADR-25 startup behavior; future H4 comparisons cross this change.
- **`scrub_bringup_env` scrubs one key, not the class**: any OTHER
  ambient `AISLE_*` variable the production graphs do not pin (e.g.
  `AISLE_HEADLESS`, `AISLE_SEED`) still flows through `**os.environ`
  into a measured rollout with clean attestation. Generalizing to an
  `AISLE_*` allowlist needs an audit of which variables campaign
  tooling legitimately passes ambiently — follow-up, not this PR.

## Acceptance

`tests/graph/test_bridge_determinism.py::test_observation_stream_is_reset_anchored`
(CON-5, BRG-1): two runs whose two resets arrive at different live wall
times must produce `reset_done[0]` at sim 0 and bit-identical
post-reset physics streams (payload sha256 sequence + reset-relative
stamps) per topic. `tests/unit/test_cmd_coalescing.py::
test_step_without_reset_defaults_off` pins the default.
