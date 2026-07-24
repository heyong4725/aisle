# SPEC 300 — Powder transfer & weighing family (bench suite, P0–P4)

Status: DRAFT, GATED — no task beyond T20 (spike) may start until the SPIKE gate
(PW-0) is decided by human ADR. Class B (spec itself Class C per CON-14).
Design-doc anchor: §12. Depends on: SPEC 010, 310 (contract ext), 040 (verifier
conventions), 050 (registry ext), 070 (rollout tiers).

## Gate

- PW-0 (SPIKE): `tools/spikes/powder_spike.py` MUST measure, on this machine
  (Metal) and optionally CUDA: (a) particle-sim steps/sec for a vessel of
  N∈{5k,20k,50k} particles with solver ∈ {MPM, PBD/SPH as available};
  (b) scripted-scoop transferred-mass repeatability (std/mean over 20 seeded
  scoops); (c) qualitative pile/pour sanity (angle of repose forms; no
  explosion). Results land in `docs/decisions/ADR-powder-spike.md` with a
  human go/no-go and the chosen solver + particle budget. All PW thresholds
  below marked TBD-SPIKE are filled by that ADR via one spec-change PR.

## Scene & oracle

- PW-1: Bench scene extends SPEC 020 conventions: fixed-base arm (franka
  profile), two balance stations (source vessel on balance A, receiving vessel
  on balance B), tool rack with ≥3 scoop/spatula tools of graded capacity,
  vessel rack. `build_bench(seed, material_cfg, ...)` pure per SCN-1.
- PW-2: Powder = particle system; `scenes/materials.toml` defines named
  materials as parameter sets {particle_size, density, friction, cohesion},
  seed-sampled for randomization tiers. No material params inline (SCN-2 spirit).
- PW-3: Sim oracle mass = particle count in receiving vessel × particle mass —
  exact by construction. Published as `oracle_mass` (verifier-only, VAL-6 rule
  extends to it). The realistic channel is `balance_mass` (SPEC 310), which in
  sim = oracle mass + configurable noise/drift model; on hardware = the real
  instrument. The ENPIRE loop MUST consume `balance_mass`, never `oracle_mass`.
- PW-4: Honest-scope rule (normative for docs and papers): sim results for this
  family validate CONTROL STRATEGY and ARCHITECTURE (coarse-to-fine dosing,
  overshoot recovery, tool selection, closed-loop convergence), not absolute
  milligram fidelity. Any milligram-precision claim MUST cite hardware runs.

## Task tiers

- PW-5 (P0): scoop-and-dump — transfer ANY nonzero mass source→receiver without
  spill > TBD-SPIKE mg outside vessels. Sanity tier.
- PW-6 (P1): open-loop target — transfer target mass ±10%, single strategy,
  known material.
- PW-7 (P2): closed-loop target — target mass ±1% via multi-scoop with
  balance feedback; overshoot beyond tolerance triggers redo-cycle (reset,
  re-dose) per the workstation's spec; success counts final state only,
  redo count reported.
- PW-8 (P3): material randomization — P2 criteria over seed-sampled materials
  (PW-2); reports per-material-cluster success (the robustness claim).
- PW-9 (P4): autonomous tooling — agent-composed graphs choose tool per
  target mass/material via the tool-changer service (SPEC 310); success = P2
  criteria + tool-choice logged as a decision observable.

## Verifier & failure classes

- PW-10: `judge_mass(balance_stream, oracle_mass, goal, t, cfg)` — pure function
  (VER-1 discipline). Success = |final_mass − target| ≤ tol AND spill ≤ limit
  AND t ≤ timeout. New failure classes: overshoot_unrecovered, undershoot,
  spill, tool_stall, oscillation (non-converging dosing loop).
- PW-11: Continuous-quantity scoring: episodes report {final_error_mg,
  n_scoops, n_redos, spill_mg, t_end} alongside binary success — dosing
  efficiency curves are the family's headline metric.

## Reset

- PW-12: Sim reset = teleport (particle state rebuild), free. The spec RECORDS
  the hardware asymmetry: behavioral reset on real hardware means pour-back or
  material consumption with cross-contamination constraints — deferred to a
  Phase-4 spec, explicitly NOT hand-waved as solved.

## Registry additions (manifests, oracle-rung stubs first)

- PW-13: balance-reader, powder-surface-estimator (vision: fill level/topography),
  scoop-controller (parameterized scoop primitive: approach, rake, oscillate,
  tilt-pour), dosing-planner (closed-loop strategy node — the agent's main
  iteration surface), tool-changer-client. Deliberate gap: NO pre-built dosing
  strategy beyond a naive fixed-scoop baseline (the agent must author better ones).

## Acceptance

- tests/sim/test_powder_scene.py::test_material_cfg_seeded (PW-1,2),
  ::test_oracle_mass_exact (PW-3)
- tests/unit/test_judge_mass.py — table-driven incl. every PW-10 class alone,
  redo-cycle semantics (PW-7), tolerance edges
- tests/accept/test_p0_expert.py::test_scripted_scoop_transfers_mass — expert
  graph moves nonzero mass, spill within limit (family integration gate)

## Out of scope
Humidity/electrostatics modeling; GMP compliance; multi-arm; liquid handling.
