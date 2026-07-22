# ADR-13 — Mobile base modeling: kinematic base (T11, SPEC 210)

Status: accepted (CON-15: SPEC 210 does not fix the base model; the agent
picks and records). Task: T11. Specs: 210 (MOB-1..MOB-5), extends 010/080.

## Decision

The `mobile` embodiment uses a **kinematic differential-drive base**, not
a physically simulated one. The bridge integrates the base pose from
`base_cmd` and repositions the arm's mount each tick; it does not simulate
wheels, tyre friction, or motor dynamics.

- State: `(x, y, yaw)` in the store frame (MOB-5), integrated per tick
  from the latest `base_cmd = (v, omega)`:
  `yaw += omega*dt; x += v*cos(yaw)*dt; y += v*sin(yaw)*dt` (unicycle).
- The Panda MJCF is re-based each tick: the arm's world mount is set to
  the integrated base pose, so arm topics (TC-5) are unchanged in the
  base frame while the base frame itself moves in the store frame (MOB-4).
- `base_pose` (MOB-1) is published from the integrated state; `base_scan`
  is a raycast from the base origin against the store geometry.

## Why kinematic, not physical

- SPEC 210's contract is about TOPICS and the guard mutex (MOB-1/MOB-3),
  not locomotion fidelity. A kinematic base honors every topic and rule
  at a fraction of the build/step cost, and stays Metal-safe (CON-1).
- A physical diff-drive base needs a bespoke base+arm asset (URDF with a
  planar joint or wheel joints) and contact tuning — weeks of asset work
  (cf. the so101 asset blocker, ADR-6) for fidelity the T0/retail tasks
  do not exercise.
- Determinism (CON-5): pure integration from `base_cmd` with an injected
  dt is bitwise-reproducible; wheel contact dynamics would not be.

## Consequences / follow-ups

- The base does not collide physically; keep-out is enforced by the guard
  (MOB-3 keep-out zones, `min_shelf_dist_m`), not by contact. The guard
  becomes the sole base-safety mechanism — consistent with SPEC 080's
  "clamp, never crash" stance.
- A future Phase-4 hardware/physical-base pass swaps the base driver
  behind the same MOB-1 topic contract (the CONTRACT.md discipline).
- `base_scan` fidelity is a flat 2-D raycast; no multi-echo/noise model
  until DR is extended (out of scope for MOB-1).
