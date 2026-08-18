# ADR-42 — Nexus feasibility: NO-GO for the desk/retail families, candidate for the bench

Status: ACCEPTED 2026-08-18 (issue #281, the gating spike). Desk/retail
verdict is **NO-GO at this time**; a redirect to the powder/bench family is
recorded as a live option, not a decision.

## What Nexus is

[dimforge/nexus](https://github.com/dimforge/nexus) — *"a GPU-accelerated
multiphysics engine, running compute shaders via WebGPU"*, positioned as the
GPU counterpart to Rapier. Rust, native + WASM, Python bindings via PyO3.

Documented capabilities: rigid-body dynamics (colliders, ball/fixed/prismatic/
revolute joints), contact resolution, **Material Point Method** for deformable
and granular materials, URDF/MJCF import through the Python bindings, headless
execution. The repository states plainly: *"still under heavy development and
is still missing many features."*

## The finding

**Nexus is a physics engine, not a simulator.** It has a viewer window for
humans. It has **no camera or sensor simulation** — no programmatic RGB, no
depth.

That is the difference between Nexus and Genesis, and it is decisive for the
desk and retail families. It is not a maturity gap that will close by waiting
for a version bump; sensor simulation is a different product surface.

## Why that is disqualifying, precisely

The topic contract (SPEC 010) requires the bridge to publish `rgb_overhead`,
`rgb_wrist` and `depth_overhead`. Three separate things break without them,
and none is optional:

1. **VER-8 stage 0 refuses to judge.** The realistic verifier is fail-closed
   on an absent or malformed calibration block. No cameras, no block, no
   verdict.
2. **VER-9 identity** is per-camera detection on rendered RGB. This is the
   stage that enforces the wrong-object latch — the safety property the whole
   asymmetric-penalty design is built around.
3. **VER-10 containment** is tray-volume containment from **overhead depth**.
   Explicitly depth-only by ADR decision (D4).

And the perception ladder (TC-9) is rungs L1/L2 *on rendered pixels*. Without
rendering, only L0 exists — ground-truth poses — which is the rung that
measures the least.

A simulator that cannot feed the frozen verifier produces runs the frozen
verifier cannot score. That is not a bridge that needs writing; it is a
family the environment cannot host.

## The redirect worth taking seriously

Nexus's MPM solver for **granular materials** is a strong match for the
powder/bench family (SPEC 300/310), and the reason is the verifier:

> PW-3: Sim oracle mass = particle count in receiving vessel × particle mass —
> **exact by construction**.

The bench family's oracle is **mass-based, not vision-based**, and its
realistic channel is `balance_mass` — a simulated instrument, not a camera.
The camera gap that disqualifies Nexus for the desk is largely irrelevant
there, while the capability that family most needs — particle throughput for
granular media — is precisely what a GPU MPM engine is for.

Design doc §12 already concedes the bench family's constraint is that
*"powder is granular media"* and that Genesis's particle solvers make it
simulatable only *qualitatively*, with a PW-0 spike gating the family on
particle throughput and solver choice.

**So the honest reframe: Nexus is not a Genesis replacement. It may be the
right engine for the family Genesis is weakest at.** That is a question for
PW-0 to answer, not this ADR.

## Consequences

- Issue #284 (Nexus bring-up for the desk) is closed as not-feasible rather
  than deferred. Reopening it requires Nexus to gain sensor simulation, which
  is a change in what the project is.
- Issue #283 (composite `env_hash`, sim backend protocol, per-sim layout)
  **loses its urgency but keeps its merit.** It was justified by "a second
  simulator is coming"; that is now false for the desk. The monolithic hash
  and the Genesis-hardcoded `resolve_sim_identity` remain real limitations,
  and the first genuine second environment — a bench-family engine, a
  world-model env (§7.5), or hardware — will need them. Rescoped, not closed.
- **Isaac Sim is unaffected by this finding.** It has full sensor simulation
  and remains what the design doc scoped it as: stage-2 validation, Linux+RTX,
  not the inner loop.
- The declared sponsorship interest (issue #284) is moot for the desk and
  would apply again if the bench redirect is taken.

## What would change the verdict

Sensor simulation in Nexus — programmatic RGB **and** depth with a calibration
block convertible to VER-8's v1 conventions. Short of that, pairing Nexus's
physics with a separate renderer is possible in principle but means owning a
rendering pipeline and re-deriving camera calibration, which is a larger
project than the bridge it was meant to avoid.
