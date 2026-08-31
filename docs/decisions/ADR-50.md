# ADR-50 — The causal control is a brokered single-module monolith

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #344.

## Decision

AISLE's primary engineering ablation will compare the typed manifest/dora graph
surface with one ordinary orchestration module. Both arms receive the same
pinned primitive implementations, semantic observations, action authority,
development and episode budgets, environment, reset, guard, verifier, scorer,
and base evidence. The monolithic arm receives ordinary Python and runtime
errors, but no capability registry, manifest resolver, dataflow YAML, AISLE
validator, structured validation catalog, or generated substitute.

A frozen controller outside the deliverable owns simulation/device and
evaluation handles. Monolithic actions cross a narrow primitive broker and the
same frozen guard used by typed actuation. The issue #353 external confinement
boundary prevents either deliverable from reaching trusted endpoints by another
path. This keeps the scorer, reset, guard, limits, hidden seeds, and evidence
sink outside both treatments without trusting an editable Python module to
police itself.

Two independently frozen human expert deliverables must pass one pre-registered,
paired-seed functional and score-equivalence gate. This is infrastructure
evidence only: expert and shakeout records carry a separate campaign purpose
and cannot enter pilot or confirmatory estimates.

## What the comparison isolates

The treatment is the bundled engineering surface: manifest/graph composition,
registry resolution, static schema/topology validation and its diagnostics, and
the dora runtime representation versus single-module orchestration with ordinary
runtime feedback. A machine-readable treatment table exposes every necessary
instruction, documentation, transport, and launcher difference.

The comparison does not separately identify the effect of static constraints,
actionable hints, graph modularity, or runtime transport overhead. It does not
show that arbitrary production monoliths are interchangeable, remove model
stochasticity, or provide hardware evidence. A later typed-generic arm may
isolate teaching hints, but it is not silently folded into this primary pair.

## Alternatives rejected

- A3 as the control: both A3 arms remain inside AISLE and therefore cannot test
  typed dataflow against ordinary monolithic orchestration.
- A second dora graph with fewer checks: it retains the graph/runtime treatment
  and provides no genuine single-module comparator.
- An unrestricted monolithic process: it could bypass the guard or evaluator,
  making capability parity unauditable and the treatment unsafe.
- Exact trajectory identity as parity: expert strategies may differ while
  exposing equal functionality; paired functional thresholds, a frozen score
  equivalence margin, interface equality, and common safety limits test the
  relevant capability instead.

## Gate

SPEC 440 is implemented tests-first only after this spec-change and the issue
#353 treatment-integrity boundary are approved. Human approval of this ADR does
not count as expert parity. No pilot begins until both expert artifacts,
confinement and bypass tests, common-evidence checks, and the immutable parity
report pass; none of those records may later be called experimental data.
