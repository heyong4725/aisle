# ADR-53 — Safety evidence uses exact exposures and fixed proposals

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #351.

## Decision

AISLE will report five distinct safety-evidence layers: declared topology,
gateway kinematic enforcement, observed kinematic outcomes, verifier semantic
detection, and semantic authorization. Results and prose may not substitute one
for another. In particular, the budget guard has no medicine identity and a
verifier observing a wrong delivery did not prevent it.

A controller-owned ledger will reconcile episodes, gripper-derived manipulation
attempts, verifier-observed deliveries and identities, trusted collision/contact
events, every command proposal, gateway decision, driver receipt, workspace
proposal/intervention/receipt/outcome, and exclusions. Classical and learned
proposal/intervention rates use frozen content-hash provenance. Missing contact
or state instruments are `unmeasured`, not zero. Exact one-sided zero-event
bounds always name their exposure denominator.

The causal guard ablation replays identical content-addressed proposals and
contract timestamps from identical initial states. `guard_on` enforces; an
evaluator-only `guard_observe_only` computes the same decision but forwards a
well-formed request unchanged to a fake driver or isolated simulator. The trace
set includes legal negative controls, kinematic violations, and motion held past
the watchdog deadline. Trace/session pairs are the independent units; individual
commands are mechanism rows, not replicates.

Observe-only is never exposed to an agent and never run on hardware. Any
emergency-containment intervention invalidates the pair. Hardware-specific
contacts, forces, stop behavior, and exposure counts remain pending until a
calibrated physical instrument produces them.

## Alternatives rejected

- Report only guard `violation` rows: one proposal can generate multiple joint
  rows, inflating exposure and intervention rates.
- Use episodes alone as every denominator: wrong deliveries and collisions have
  more interpretable delivery/manipulation exposure units that must be explicit.
- Compare autonomous guard-on and guard-off policies: plan variation confounds
  the guard effect; fixed proposal traces isolate enforcement.
- Call zero observed wrong objects structural prevention: the guard does not
  know identity, and the verifier reports after the semantic event.

## Gate

SPEC 470 is implemented tests-first after this spec-change and its #345/#350
dependencies merge. Human approval does not create exposure data. No H5 safety
claim is promoted until raw-ledger reconciliation, fixed-trace ablation,
zero-event bounds, source strata, and wording checks pass. Physical rows and
claims stay open until real hardware produces calibrated evidence.
