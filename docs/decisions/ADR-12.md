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

- franka: pos x 0.50, level_size [0.36, 0.60], heights [0.05, 0.32],
  depths [0.36, 0.12]. L0 open band 0.14 m; L1 fully usable. Verified:
  200/200 placement seeds, 0/250 kinematic plan failures (seeds 0..49),
  physics replays L0 (seed 3 ibuprofen) and L1 (seed 0 amoxicillin,
  omeprazole) all land IN TRAY.
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
