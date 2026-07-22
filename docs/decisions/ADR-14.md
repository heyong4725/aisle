# ADR-14 — Mobile guard limits: reuse the franka arm limits (T11, SPEC 210 MOB-3)

Status: accepted (CON-15: SPEC 210 does not say where the mobile profile's
ARM limits come from; the agent picks and records). Task: T11. Specs: 210
(MOB-3), extends 080 (BG-2). Relates to [[ADR-13]] (kinematic base).

## Decision

The budget guard, for the `mobile` embodiment, loads its **arm** limits from
the `franka` section of `env/limits.toml` and its **base** limits from the
`[embodiment.mobile]` section. `[embodiment.mobile]` carries ONLY the base
limits (v_max, omega_max, v_creep, omega_creep, base_cmd_dt_s,
min_shelf_dist_m); it does not duplicate the arm limits.

- `load_limits("mobile")` resolves the arm embodiment via
  `_ARM_EMBODIMENT = {"mobile": "franka"}` and reads arm fields from the
  franka section.
- `load_base_limits("mobile")` reads the base fields from the mobile section,
  which now also carries the mutex/keep-out/nav parameters below.

`[embodiment.mobile]` fields (beyond the original velocity/creep/dt/shelf-dist
six): `arm_motion_hold_s`, `arm_extended_reach_m`, `base_staleness_s`, and the
`nav_*` lifecycle params (arrival tol/yaw, timeout/stall ticks).

## Why

- The mobile profile IS a franka arm on a kinematic base (ADR-13, MOB-4):
  its arm topics and kinematics are franka-identical, so its arm limits must
  be too. Duplicating the ~10 franka arm fields into `[embodiment.mobile]`
  would be a copy that can silently drift from the franka source.
- It mirrors the validator's existing `EMBODIMENT_ARM = {"mobile": "franka"}`
  resolution (`harness/validate.py`), so arm-limit resolution and arm-graph
  validation agree by construction.
- BG-2 forbids the guard from guessing limits: this is not a guess but an
  explicit, single-sourced mapping — the franka limits are authoritative for
  the franka arm wherever it is mounted.

## Consequences

- A future non-franka mobile arm adds its own `_ARM_EMBODIMENT` entry (or a
  full section), not a change to this mapping.

## MOB-3 base-safety model (revised after PR #14 review)

- **arm/base mutex — timed hold, not a one-message delta.** A commanded
  arm-target CHANGE opens a creep-hold window of `arm_motion_hold_s`
  (`base_creep_deadline`); the window persists while the arm travels even if
  the same target repeats, and expires on command silence. `arm_in_motion`
  is `now < deadline`. This fixes the earlier exact-inequality flag, which
  read false the instant a target repeated (arm still moving) and could
  latch true forever on silence. The injected clock keeps it deterministic
  (CON-5).
- **keep-out — fail closed, prevent ENTRY.** The arm counts as reaching when
  the FK flange exceeds `arm_extended_reach_m` (home ~0.31 m does not, so the
  base can still approach a shelf to set up). Forward velocity is then capped
  to the remaining clearance / `base_cmd_dt_s` so one step cannot cross into
  `min_shelf_dist_m` (footprint included). With no `base_pose` feedback the
  base is held at 0 (fail closed). The cached pose clears on reset.
- **watchdog.** The base handler applies the BG-2 wall timeout, and a tick
  watchdog emits `[0,0]` once a command goes stale (`base_staleness_s`) or
  the episode times out, so a latched command cannot integrate forever.
