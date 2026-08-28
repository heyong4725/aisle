# ADR-phase6-prep — the hardware entry, prepared while gated

Status: ACCEPTED (CON-15). Date: 2026-08-27. Scope: next-phases
"Phase 6 — hardware"; SPEC 010's sim→real claim ("this contract is
what makes sim→real a node swap"); VER-8's hardware addendum; owner's
standing order ("continue the loop, start phase 6 prep").

## Entry-criteria gap ledger (measured, not aspirational)

| Criterion | State |
|---|---|
| M5 wrong-medicine 0 sustained | **MET** — zero across every episode ever run, including all VLA-driven arms |
| M1 hybrid ≥ classical on T1/T2 in sim | **GATED** — the live policy has no task competence at the current training dose (M1 lockstep-eval); unlock = training dose (GPU or long-CPU), each dose measurable at ~17 min/episode |
| vlm-verifier fidelity ≥ detector verifier | **GATED** — five judge configs refused (analysis/ver-vlm); unlock = wrist/hi-res judged frames (design change) or fine-tuning (GPU) |

Phase 6 therefore does NOT open with this ADR. What this ADR lands is
the preparation that makes the eventual swap a driver node in fact:

## 1. The SO-101 driver node (src/aisle/nodes/so101_driver.py)

Speaks the bridge's hardware-relevant surface — `joint_state` /
`gripper_state` out at the TC-4 cadence, `joint_cmd` / `gripper_cmd`
in, TC-2 stamps — with hardware time as the clock source (no
`sim_time_ns` fabrication: hardware runs free-run; ADR-30 lockstep is
sim-only by design and stays out of the driver).

- **Reset refuses honestly** (TC-6/ADR-34): hardware has no teleport;
  the driver answers `reset_refused` with the reason and Phase 6
  episode boundaries ride the behavioral-reset path (A6 measured that
  path's cost in sim: 0.80 at +19 s/episode — known, priced).
- **The bus is injected**: `AISLE_SO101_PORT=<serial>` drives a real
  `lerobot` SO101Follower (imported ONLY then — the unit suite and CI
  never touch lerobot); `AISLE_SO101_PORT=loopback` runs a pure-python
  bus double (first-order lag, the same joint order the frozen
  `embodiment.py` pins) so every contract behavior is testable today.
- **Safety at the driver**: per-tick command delta clamped to
  `max_step_rad` (defense in depth UNDER the budget guard, which
  remains the frozen authority above it); the guard's existing limits
  apply unchanged because the topic surface is unchanged.

## 2. The hardware calibration path (tools/hw_calibration.py)

VER-8's addendum names `env/calibration.toml` as the per-device
measured artifact. The tool builds the v1 block from it via the SAME
`build_calibration_v1` the sim bridge uses and validates it with the
SAME stage-0 refusal predicates — fail-closed parity between sim and
hardware from day one. A template documents every measured field.

## What is deliberately NOT here

- No spec edits (the TC contract already covers the surface; CON-14).
- No guard/limits edits (frozen set; hardware limit VALUES arrive
  with the hardware, through human review as CON-7 requires).
- No camera drivers (lerobot's camera stack exists; judged-frame
  geometry is the 5.2-recorded design question and belongs with it).
- No entry-criteria waivers: the gates above stay as written.
