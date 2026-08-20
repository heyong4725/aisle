# T2 breakthrough r3 — agent 1 campaign notes (in progress)

## What I inherited
- Expert T2 baseline: pass1 0.08 (run 20260811-173916), 15/25 never_grasped
  (tour starvation), 7 collision, 1 dropped, 0 wrong_object ever.
- r2 (arm L) skills staged in skills_pending_review/: t2-scan-pose (candidate
  positions WITHOUT l2_pose's identity gate — at T2 identity is color noise by
  design; fixes 5/10 episodes that never toured) and t2-scan-tsm (per-candidate
  z for cross-level candidates, board-snap z on promotion, planner neighbour
  re-aim, confirm-margin 0.10, tour restarts).

## What I did (chronological)
1. **Ported r2 skills to ADR-30 lockstep** (they predate it): swap
   `from dora import Node` → `from aisle.turn_node import Node` in both; in
   t2_scan_tsm main add the stock TSM's turn-based 1 Hz tick synthesis
   (`event["id"] == "turn"` → sim-time-driven on_tick) and gate the timer tick
   on `not lockstep`. The turn_node wrapper handles turn_done automatically.
2. **Registration**: `harness skill register <dir> --sandbox` (ADR-40) is the
   campaign-iteration path — no eval run needed, but eval.yaml min_pass_rate
   must be ≥0.5 (registry floor) and the eval graph must contain a node whose
   id EQUALS the skill id. So agent_campaign.yaml uses node ids t2-scan-pose /
   t2-scan-tsm. Manifests need lockstep ports: input
   `turn: {schema: sim_turn_u64, rate_hz: 100, is_clock: true}`, output
   `turn_done: {schema: sim_turn_u64, latency_class: hard_rt}`, and
   `turn_edge: episodic` on reply/verdict back-edges (violation, target_pose,
   move_done, read_result, plan_done) or validate fails CLOCK_CYCLE.
3. **Probe rollout (I1, run 20260819-222102, seeds 0-1)**: 0/2, both
   `collision`. Diagnosis from oracle_state displacement analysis:
   - r2's tour-starvation fix WORKS (tours start, reads are healthy: correct
     mismatch read at margin +0.40).
   - Bug: tsm's PITCHED_LADDER_OFFSET=3 indexes the SOLVABLE-solutions list,
     not the ladder — for low faces the pitched rungs are often dropped (no
     staged-pose IK), so offset 3 lands on flat 0.24 (jam, err ~1.0-1.2 rad)
     or exhausts a <4-entry list instantly. 4 candidates lost this way in 2 eps.
   - Collision cause both eps: the 0.16 m pitch-0.35 park on a +y face knocks
     the box ~1.5 s into the park (mid home→read-stage hop, elbow/forearm
     sweep at shelf height). Jam PRESSES displaced nothing (contrary to r1
     fears); TRANSITS are the collision source.
4. **Fix bundle (I3)**, in editable code (ik_trajectory.py is NOT frozen):
   - read_move payload flag `pitched_first` (content-based reorder of the
     solvable list, pitched rungs first) replaces the index offset.
   - _PITCHED_FIRST_LADDER prefers 0.20 m pitched over 0.16 m.
   - Pitched entries get a transit waypoint: far pose lifted +0.12 m
     (READ_HIGH_LIFT_M), IK'd best-effort, prepended to the read-stage path.

## Operational lessons (read these first, they cost me ~40 min)
- Bash tool calls are hard-capped at 10 min wall regardless of requested
  timeout. Run `harness rollout` with run_in_background + TaskOutput
  block-polling (600 s per call) instead of one synchronous call.
- The harness stall detector (PRE_DATA_STALL_S=600) kills a launch whose sim
  produces no trace data within 10 min. With 2+ concurrent peer sims on this
  machine, Genesis startup can exceed that. A stalled run settles 0 episodes
  (reservation returned); the wall time is the real loss. Retry when fewer
  peer dataflows are running (`pgrep -fl "dora run"`).
- First-ever rollout downloads HF model weights (~1 min) — budget for it.

## Results
- (pending) I3 rollout seeds 0..5, run 20260819-225754-8156ca.

## Next ideas (untested)
- Shorten STAGE_BAIL_S (4 s) presses during read stages only — jams burn
  ~8-12 s per failed attempt against the 150 s episode budget.
- Tour-order: read cheapest/safest faces first (flat -y faces), +y faces last
  (already partially true via y-sort within a level).
- If pitched 0.20 parks still knock: raise READ_HIGH_LIFT_M, or add an
  azimuth-offset descent for +y faces.
