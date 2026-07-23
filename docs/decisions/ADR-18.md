# ADR-18 — S1 expert graph design (T15, SPEC 200 acceptance)

Status: accepted (CON-15: SPEC 200 names the gate but not the expert's
architecture; the agent picks and records). Task: T15. Relates to
[[ADR-13]] (kinematic base), [[ADR-15]] (store model), [[ADR-16]]
(retail verifier), [[ADR-17]] (capabilities).

## Architecture

1. **The bridge serves the store behind the same topic contract.**
   `AISLE_SCENE=store` builds via `build_store`; oracle/poses come from
   the stock roster, base_scan/keep-out obstacles from the planogram
   (`store_scan_obstacles` — the guard follows `AISLE_SCENE` too).
   Store is mobile-only, single-env, and S1-only through reset: a
   TELEPORT reset cannot add/remove entities, and only S1 keeps stock
   constant across seeds (S2/S3 rollouts need a rebuild per episode —
   deferred).
2. **Park-pose strategy.** The expert navigates to a COMPUTED pose
   `PARK_STANDOFF_M` in front of each source slot, so every pick happens
   at the same desk-like base-frame geometry the proven grasp/ik stack is
   tuned for. The standoff (0.48) and the nav arrival tolerances
   (0.05 m / 0.05 rad) are coupled: the pick chain must SOLVE at the
   worst arrival corner (regression-swept in unit tests with real IK).
3. **Split pick/place around navigation.** The desk's fused
   `StagedPlan` cannot span a nav leg, so the expert builds a pick half
   (rise…retract + a Cartesian carry tuck, grip held) and a place half
   (wrist unwind, transfer, converge-lower, stationary release) from the
   SAME pure helpers with the same tuned settle/vel/track_tol values,
   executed by `StageStreamer` — the per-joint_state step extracted
   UNCHANGED from ik-trajectory's main and shared by both nodes.
4. **Wrist yaw discipline** (live-run findings): the item yaw folds into
   (-pi/2, pi/2] before grasp planning (box pi-symmetry) so J7 stays near
   home — an unfolded yaw near pi commanded a J7 spin past its limit and
   the grip closed 41 degrees misaligned. The carry HOLDS the grasp
   orientation (a slerped flip over the short carry fails); the flip to
   the neutral place wrist is a PURE-J7 unwind at the carry point — J7 is
   coaxial with the wrist-down flange, so it is minimal motion by
   construction (no IK branch-hopping), with box-symmetric residuals.
5. **L1 sourcing.** v0 store units are uniform-depth, so lower levels
   sit under a board — top-down picks are physically impossible there
   (the T10 lesson). task-planner sources from the HIGHEST level first;
   the fixed-seed gate (seed 1) orders products with L1 stock. Staggered
   store units (desk-style) are the follow-up that unlocks L0.
6. **Counter at 0.55 m.** A FLOOR-mounted franka cannot wrist-down place
   at 0.9 m + box height (probed: the wrist-down envelope tops out
   ~0.78 m at the drop x, and the place transfer hovers +0.10 for the
   same reason). A raised arm mount is the Phase-4 fix.
7. **Nav robustness.** `base_staleness_s` 0.5 (under store-sim
   backpressure, rtf ~0.1, dora timers stretch and a 0.1 s watchdog
   zeroed the base between nav commands); the expert RETRIES a failed
   nav leg twice before idling (never skips a pick — round 2 "placed"
   nothing after skipping a stalled pick leg).
8. **The oracle ladder is exercised end to end**: the expert consumes
   order-reader's order and task-planner's plan (not the goal's raw
   fields), waypoint-nav drives the base, verifier-retail (the
   `verifier-` prefix authorizes oracle_state, VAL-6) scores per RS-6/7,
   rollout `--tier S1` sources goals from the episode generator.

## Known limits (v1)

- Store rtf ~0.1 on the dev machine: the acceptance episode runs
  ~15 wall-minutes; the gate budgets accordingly (nightly-suite scale,
  like the M0 gate).
- Grasp success depends on the wrist converging before `close`
  (bail-advance covers transient lag); no regrasp on a failed grip.
- S2/S3 end-to-end rollouts deferred (bridge rebuild-per-episode).
