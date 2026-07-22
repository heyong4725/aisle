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
- `load_base_limits("mobile")` reads the base fields from the mobile section
  (unchanged).

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
- The MOB-3 mutex "arm in motion" reference is the guard's own commanded-pose
  delta (exact inequality on the safe arm pose — a hold repeats the exact
  target), keeping the rule deterministic (CON-5) and threshold-free.
