# ADR-12 — T10: staggered two-level shelf (M0 env-change) and the M0 gate suite

Status: accepted (owner decision: "Two-level shelf" option, 2026-07-18).
Task: T10. Specs: 090 (M0-1..M0-6), 020 (SCN-2/SCN-3), touching CON-5/CON-7.

## 1. Why the environment changed

ADR-10 §8 recorded the coverage gap: on the three-level shelf, only the
top level was reachable by the proven top-down grasp — any vertical
descent to a lower level crosses the full-depth board above, and the
front-approach fallback was refused by FLIP_MAX (the wrist flip is
dynamically unstable, ADR-10 §12). A 50-placement probe on the frozen
geometry showed 47/50 planning failures. M0-1 (pass1 >= 0.95 over seeds
0..49) was unreachable by policy improvement alone; the owner approved an
environment change (pre-M0, the set is not yet frozen: CON-7 permits).

## 2. The staggered design

Two levels, rear-aligned boards, the upper board SHALLOWER than the lower
(display-shelf style). Each level's sampling band is its own board span
minus every higher board's span — so every sampled box has open sky and
the SAME proven top-down grasp works on both levels. FRONT-mode remains
in grasp-planner-topdown as a safety net for out-of-band poses (now a
pure module-level `needs_front(x, z, shelf)`): a box needs it only when
below a higher board within that board's span PLUS the HAND_CLEARANCE_M
strip (§3), with < HAND_COLUMN_M vertical clearance. Unit tests pin the
sampler/planner agreement: no sampled placement across 200 seeds and
both embodiments ever triggers it.

## 3. Hand clearance (found by physics replay, not kinematics)

The first staggered attempt failed in replay: with the box at the band's
rear limit, the descending hand (half-extent ~0.045 m plus ~0.05 m
tracking transient) landed ON the upper board's front edge and froze
genesis (constraint NaN). Kinematic planning cannot see this — the IK
plan was valid. `open_band` therefore reserves `HAND_CLEARANCE_M = 0.10`
from a higher board's front plane in addition to the overhang. This is a
scene-sampler constant, deliberately conservative for both embodiments.

## 4. Final geometry (physics.toml)

- franka: pos x 0.50, level_size [0.36, 0.66], heights [0.05, 0.32],
  depths [0.36, 0.12], tray y -0.45 (clear of the shelf span, §5a),
  min_separation 0.04 (§5a). L0 open band 0.14 m; L1 fully usable.
  Verified: 200/200 placement seeds, 0/250 kinematic plan failures
  (seeds 0..49), physics replays L0 and L1 land IN TRAY.
- so101: pos x 0.22, level_size [0.30, 0.50], heights [0.02, 0.24],
  depths [0.30, 0.06]. The upper level is geometry-only: the reach
  pre-filter excludes it (0.45 m arm), and all five meds place on L0's
  band across the widened 0.50 m span (200/200 seeds). so101 remains
  asset-blocked (ADR-6); M0-5 is authored but skip-marked.
- STAGING_Z lowered 0.66 -> 0.56 in ik-trajectory: the max box top on the
  new shelf is 0.44, and deep rear staging poses at 0.66 failed IK.

## 5. Sampler band-fit guard

A level whose open band cannot fit a med (band minus margins narrower
than the box) is skipped for that med rather than sampled with inverted
bounds — random.uniform silently accepts reversed bounds and would place
boxes outside the band.

## 5a. What the first two M0-1 attempts taught (both aborted early)

- Run m0-1-e634e4 (aborted at ep 8): the widened shelf overlapped the
  tray's wrong_object entry footprint — boxes could START inside it and
  the verifier correctly failed those episodes at t=0. Trays moved fully
  outside the shelf span in y; a regression test pins the
  disjoint-footprint invariant.
- Run m0-1-e2e07b (aborted at ep 16, 6 fails): three mechanisms, all
  found from the run's own Arrow traces (HAR-4/HAR-6 doing their job):
  1. STALE-STATE DIVE (the big one): the reset fires on the verifier's
     verdict, not on plan completion, so the executor can be mid-plan
     when the scene teleports. With joint_state queue_size 100 the
     executor later seeded its interpolation start from a queued
     PRE-reset frame and drove the arm from home THROUGH the fresh scene
     (TCP dipped to z=0.025 sweeping the shelf front — episodes 12/13
     cascade, and the same nudge class explains the off-center
     drag/timeout/drop failures of eps 3/8/15). Fix: joint_state is
     state, not a command stream — queue_size 1 (latest-wins) in the
     expert graph.
  2. FINGER-SWEEP KNOCK: a 0.022 m neighbor gap sits inside the open
     fingers' sweep envelope (±0.04 half-travel + tip) and the descent
     knocked the neighbor (ep 11, collision). Fix: franka
     min_separation 0.04 (shelf width 0.60 -> 0.66 to keep all 200
     placement seeds satisfiable); min_separation became a
     per-embodiment override (pregrasp_height_m pattern) because
     so101's small gripper needs no such clearance and its 0.45 m reach
     cannot afford a wider shelf.
  3. The teleport already zeroes box velocities and restores
     orientation — those were ruled out by reading the bridge, not
     assumed.

## 5b. Grasp-physics reliability (offline 50-pair sweep, 19/50 -> 48/50)

After the geometry/queue/clearance fixes closed the instant-fail and
knock classes, the remaining M0-1 failures were release-time TOPPLES —
the box reached the tray footprint (IN TRAY true) but landed on its side
(oracle upright_max_deg, a lying box never verifies). An offline
single-process sweep of the exact 50 (seed, target) M0-1 pairs, mirroring
the executor (per-stage track_tol + settle), isolated four independent
causes, each found by frame-level box-quat + render telemetry, not guesswork:

1. PALM CONTACT: GRIP_ENGAGEMENT 0.045 assumed 5 mm palm clearance from
   0.05-long fingers, but the palm plate actually pressed the box top and
   shoved it sideways during descent — spinning near-square meds into
   diagonal detents at close and ratcheting a pitch tilt through the
   carry. Renders of the cetirizine grasp showed it directly. 0.035
   keeps the palm genuinely clear; the T08 "shallow grips pitch" note was
   this same contact misattributed.
2. OPEN-WHILE-RISING RELEASE: lifting the still-gripped box during the
   ~1 s finger-open ramp gave it pendulum energy and it slipped off
   raised, toppling tall meds. Replaced by a STATIONARY open at a hover
   pose (release path = the lower pose held), settle covering the full
   ramp. The old shear concern applied to a SEATED box; the box now
   hovers PLACE_DROP_GAP (0.02) above the slab instead of resting.
3. JOINT-SPACE TRANSFER SWING: the transfer stage was a bare joint
   waypoint, leaving TCP orientation unconstrained mid-swing — the wrist
   tilted, gravity torqued the box about the pinch line, and it
   creep-rotated flat before release (slower swings made it WORSE, more
   time tilted). Rebuilt as a Cartesian ik_continuation that holds the
   place orientation across the swing.
4. DROP GAP vs GRIP: transient values of PLACE_DROP_GAP were retired once
   the box stayed axis-aligned in the grip; 0.02 with the converged
   stationary release lands flat-bottomed boxes upright.

Result: 48/50 offline (0.96), the two residual tips are omeprazole (the
near-cube 0.05x0.045 med) on specific placements — right at the margin.
The live 50-episode graph run is the ground-truth gate; live has run
slightly harder than this offline proxy, so 0.96 offline is not a
guaranteed live pass and the run result governs.

## 5c. Reset-boundary bugs found by the live gate (pass1 0.84 -> 0.98)

The first full 50-episode run held ~0.85, failing on `collision` verdicts
that OFFLINE physics never reproduced (single-episode and cross-episode
replays were clean). Instrumenting the live arm isolated two independent
reset-boundary bugs — neither a grasp-geometry issue, so the min_separation
"clearance" lever did nothing:

1. VERIFIER RESET-RACE (determinism): the verifier seeded each episode's
   initial box poses from the first oracle sample past
   `latest_oracle_ns`-at-goal-arrival, a barrier that only guaranteed
   "after the goal", not "after the teleport". A pre-teleport frame could
   become the baseline, so the teleport read as a mass collision —
   intermittent, and it would have broken M0-2 determinism. Fix: the
   bridge stamps `reset_done` with the teleport sim_time, the client
   carries it into the goal, and the verifier captures only from an oracle
   frame at/after it (`initial_capture_barrier`, pure + unit-tested).

2. EXECUTOR STALE-COMMAND CASCADE (the dominant failure): a collision or
   timeout ends an episode mid-plan, but the executor keeps streaming that
   plan's joint_cmds for the few ticks until it receives `reset_done` and
   clears. Those in-flight commands drove the just-teleported-home arm
   back off home, so the NEXT episode began from the previous grasp pose
   and swept the shelf, knocking a neighbor (~t=1 s). Every failure thus
   dragged its successor down — a 2x multiplier. Fix (owner-approved
   "reset-home" option): the bridge holds the arm at home and DROPS
   incoming joint_cmds for `RESET_SETTLE_TICKS` (20 ticks / 0.2 s) after
   each reset — long enough to cover the executor's reset_done round-trip,
   far shorter than the goal->grasp latency, so no real command is lost.

Result: pass1 0.980 (49/50, seeds 0..49). The single residual failure is
seed 8, an ep8-type open-finger graze: the omeprazole grasp's open
fingers straddle the target's y-axis and reach ~4 cm past its edge, where
the live pipeline's path deviation clips a same-level neighbor. This is
above the M0-1 0.95 bar; the finger-graze is a documented robustness
follow-up (grip-axis-aware separation or a narrower descent), not an M0
blocker.

## 6. M0 suite interpretation

- M0-1/M0-2 share one module-scoped 50-episode run; M0-2 performs the
  full second run (the spec says "re-running M0-1") and compares the
  (seed, status) vector. Run dirs are kept under runs/ as ADR-M0
  evidence.
- M0-3 mutates the REAL src/aisle/verifier/thresholds.toml (one appended
  byte), invokes the real CLI, asserts refusal at the env_hash gate, and
  restores byte-for-byte in finally. The committed tools/env_hash.json is
  regenerated in this PR (frozen files legitimately changed pre-freeze).
- M0-4: the ADR-1 process-rule waivers (CON-10/11/14/15) are retired by
  proxy tests (tests/unit/test_process_rules.py) that pin the enforcing
  MECHANISMS: CODEOWNERS Class C coverage, conventional commit history,
  the PR template's requirement-ID section, and the ADR log's shape. The
  strict gate `trace_check.py --strict --specs 000-080` now passes.
- M0-5: `harness rollout --embodiment <e>` added (env-propagated profile
  swap, zero YAML edits, recorded in the manifest) so the spec-literal
  invocation exists. The HAR-2 gate validates against the SELECTED
  embodiment, so `--embodiment so101` refuses up front today
  (EMBODIMENT_MISMATCH: ik-trajectory is franka-only) instead of
  crashing nodes mid-run. The test skips until BOTH blockers land: the
  asset (ADR-6) and so101 support in the motion nodes (tracked by the
  ik-trajectory manifest).
- M0-6 is a human act: docs/decisions/ADR-M0.md holds the sign-off
  template; the owner fills verdict + frozen-set label.
