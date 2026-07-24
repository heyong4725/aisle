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

9. **Kinematic grasp attach (the carry mechanism).** The kinematic
   base teleports the arm each moving tick, which no physical pinch can
   survive (three designs fell in live rounds 14-18: unpinned cargo slid
   out instantly; per-tick proximity coupling lost the box on one missed
   tick; base-frame pinning destroyed the pinch and parked the box
   mid-air). The landed design is the standard sim solution: from grip
   close (fingers < 0.025) to finger open (> 0.035, hysteresis) the held
   item is kinematically attached to the HAND LINK — its hand-frame
   offset is captured at latch and re-applied every tick; physics
   re-owns the box at the drop hover. Bridge-side, observable in the
   log as `carry latch:` / `carry release:`.
10. **Nav robustness under backpressure** (rounds 8-13): rotate-phase
   omega cap (loop-delay overshoot must fit the yaw band), rotate-only
   hysteresis latch (boundary chatter), three-way phase-aware progress
   (dist driving / bearing turning / final-yaw rotating), settle-verify-
   renavigate on EVERY leg against the IK-proven tolerance, and a
   conditional re-base (a stationary base must not touch the solver).

11. **Reported base_pose IS the physical root** (PR #21). The teleport
   reset re-homed the integrator variable without moving the robot root,
   and the change-conditional re-base (item 10) then never fired — the
   physical base silently stayed at the pre-reset pose. Two-part fix:
   the mobile reset explicitly `set_pos`/`set_quat`s the root to
   base_start (and releases a mid-carry latch), and base_pose is
   published from a `get_pos`/`get_quat` READBACK, so any future
   reported-vs-physical divergence is visible on the wire and caught by
   the multi-episode reset regression (tests/graph/test_mobile_bridge).
12. **Discriminator stays closed** (spec-conflict #22, option 1). The
   retail verifier consumes privileged oracle_state, so its verdicts ARE
   countable ground truth (TC-8): episode_result carries
   `verifier: "oracle"` and retail identity rides the ADDITIVE
   `suite: "retail"` field — no schema fork, no spec change.
13. **`harness rollout` runs every tier** (RS-6, PR #21). The CLI accepts
   `--embodiment mobile`, and `tier_budgets` swaps the desk budgets
   (60 sim s / 150 wall s — they would kill a healthy retail episode)
   for retail-scale ones (600 sim s / 2100 wall s per episode) on
   S1..S3. The acceptance gate drives the PUBLIC harness path, not a
   bare `dora run`.
14. **Store camera policy** (HAR-4, PR #21 round 2). expert_s1.yaml now
   declares `rgb_overhead` so the harness records overhead.mp4.
   `store_topic_rates` keeps that stream at 5 Hz — ample for an episode
   video, and the desk 30 Hz frame transport is pure overhead at store
   scale — and drops the consumer-less wrist/depth streams (their
   renders were waste in every store run). Desk rates are untouched;
   the gate asserts the video and the rgb_overhead trace through the
   public path.
15. **Nav capture band** (MOB-2, PR #21 round 3). With the video stream
   perturbing event timing, the counter park landed 0.5 mm OUTSIDE the
   0.05 arrival radius and nav could not recover: a diff-drive base
   cannot point-stabilize onto a target it is effectively ON (bearing
   flips at mm range, progress under the detector's epsilons), so the
   leg failed `blocked` with the final yaw still ~pi off — three times,
   then idle, then episode timeout. `nav_capture_tol_m` (0.075): a
   drive-phase stall inside the band latches the final ROTATE instead
   of failing, and arrival accepts the band once latched-rotating. The
   expert's park-verify reads the same band, and the IK envelope sweep
   proves pick+place solve at the CAPTURE corners, so every pose nav
   can accept stays solvable. Green runs were landing 1 mm inside the
   radius by luck; the band removes the coin flip.
16. **Near-field omega cap** (MOB-2, PR #21 round 3, with item 15). The
   capture band alone was not enough: the drive controller ORBITED the
   counter (tap: dist 2.43 -> 0.19, then RISING to 0.27, yaw swinging at
   saturated omega 1.5 through ~8 sim s) — near the target the bearing
   swings fast, K_OMEGA saturates, and the ~0.15 s-sim loop delay
   overshoots every swing; v self-scales with dist (K_V) but omega did
   not. Inside `nav_near_field_m` (0.25) the drive phase turns at the
   rotate-phase cap (0.3) — the same physics as the round-8 rotate fix,
   applied to the approach.
17. **Nav budgets scale with rtf** (MOB-2, PR #21 round 3, the terminal
   finding). The wall-t tap acquitted every transport suspect (base_pose
   gaps < 0.1 s wall end to end) and showed a CORRECT, converging final
   rotate — 2.3 rad at the 0.3 cap, exactly on command — killed by
   `nav_timeout_ticks` = 3000 wall ticks (60 s): nav budgets are WALL
   ticks measuring a SIM process, and at store rtf ~0.3 a sim-second
   costs ~3x the desk's ticks. 12000/400. (On real hardware wall == sim
   and these shrink back — the sim-relaxation stance from PR #14.)

## Known limits (v1)

- Store rtf ~0.1 on the dev machine: the acceptance episode runs
  ~15 wall-minutes; the gate budgets accordingly (nightly-suite scale,
  like the M0 gate).
- Grasp capture is verified by the carry latch engaging; no regrasp
  loop on a failed grip (the settle-verify gate makes misses rare).
- S2/S3 end-to-end rollouts deferred (bridge rebuild-per-episode).
