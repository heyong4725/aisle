# ADR-54 — Semantic prevention requires a separate trusted authorizer

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #352.

## Context and alternatives

The budget guard knows commands and kinematic limits, not the requested medicine
or carried object. The verifier can identify an observed wrong delivery only
after the semantic event. Three evaluation alternatives therefore remain
materially different:

1. `no_shield` is the honest existing baseline. It can measure observed events
   but offers no semantic prevention mechanism.
2. `oracle_sim_shield` uses simulator object and attachment truth. It is useful
   as a privileged mechanism ceiling and deterministic fixture source, but is
   non-deployable and cannot support a physical or portability claim.
3. `sensor_shield` consumes deployable identity assertions and carrier
   association. It is the only candidate for a deployable semantic-prevention
   claim, and remains a candidate until physical false-allow, false-block,
   latency, bypass, calibration, and failure-recovery evidence satisfies a
   frozen protocol.

## Decision

AISLE will implement the three arms as a fixed held-plan study. A trusted
`semantic-authorizer`, distinct from policy, kinematic guard, and verifier,
issues short-lived single-stage permits. The issue #350 actuation gateway
authenticates and consumes a permit at pre-grasp closure, post-grasp carry, and
delivery-envelope entry or release. Identity must be renewed during carry and
after relevant state changes. Missing, stale, uncertain, disagreeing, or refused
identity fails closed; already-moving hardware requests a kinematically safe
halt rather than treating semantic uncertainty as a motion policy.

The primary false-allow and false-block units are independent held plans or
sessions, not frames, commands, or repeated authorization stages. Held plans,
wrong-target proposals, identity records, expected decisions, thresholds,
randomization, endpoints, power/precision, and exclusions are frozen before
execution. Policy success, authorizer interventions, guard/containment actions,
and verifier-observed outcomes remain separate measurements.

The sensor contract forbids simulator ids and records physical assumptions about
placement, vocabulary, marking, occlusion, lighting, association, clocks,
latency, keys, and recovery. Until calibrated hardware data exists, its physical
rows remain `hardware_pending`. If this mechanism cannot be justified under a
declared deployment envelope, H5 is permanently limited to zero observed events
with uncertainty and no prevention claim. Oracle results cannot rescue it.

## Consequences and rejected shortcuts

- A policy-provided label or commanded target cannot authorize itself.
- A correct verifier verdict cannot be credited as prevention.
- Reusing the last good identity through staleness or disagreement is forbidden.
- Independent gateway enforcement and key/process isolation are part of the
  claim; permit-shaped messages alone do not create a boundary.
- Refusal improves semantic containment but may reduce availability, so false
  blocks, latency, and policy success remain visible rather than optimized away.

## Gate

SPEC 480 is implemented tests-first only after this spec-change and its
#345/#350/#351 dependencies merge. Human approval creates no empirical evidence.
No semantic-prevention wording is permitted until the held-plan, bypass, and
hardware criteria pass from retained raw records under the frozen protocol.
