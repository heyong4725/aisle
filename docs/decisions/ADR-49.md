# ADR-49 — Instrument validity is a release-blocking mutation gate

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #354.

## Decision

AISLE will maintain a machine-readable inventory from each frozen primary
estimand and exclusion rule through its verifier, validator, analyzer,
provenance, and publication transforms. A catalog maps explicit mutations to
that inventory and fixes the expected detection layer and independent oracle
before execution. The audit runs pristine controls and one mutation at a time
in disposable copies, reports strict detection and false-alarm denominators,
and lists wrong-layer detections, wrong verdicts, survivors, and unexecuted
cases as blind spots.

Fixture-based recomputation of every primary analyzer will use a reference
calculation that does not share production calculation helpers or decision
constants. This guards against a test merely repeating the same arithmetic or
exclusion error. It is complementary to ordinary unit tests: the mutation
benchmark asks whether an end-to-end measurement defect is noticed at the
declared boundary.

A critical survivor, an exclusion-causing false alarm, incomplete primary
coverage, or an unresolved independent-review finding blocks protocol freeze,
confirmatory collection, and publication. Repairing an instrument invalidates
the prior pass and requires a complete new audit; the failed predecessor stays
in the evidence record. The release retains the catalog, fixtures, mutation
operators, raw case outputs, reports, reference derivations, and signed review.

## Alternatives rejected

- Existing unit tests alone: many are strong mutation regressions, but there is
  no complete primary-estimand inventory, common denominator, false-alarm
  accounting, independent recomputation boundary, or release gate.
- Aggregate mutation score: it can hide a single critical survivor and gives no
  assurance that every primary estimand and exclusion rule was exercised.
- Treat any crash as detection: an unrelated failure does not demonstrate that
  the intended instrument layer recognized the defect.
- Reviewer prose without authorship evidence or raw outputs: it cannot establish
  independence or reproduce the disposition of individual mutations.

## Gate

SPEC 430 is implemented tests-first after this spec-change merges. A reviewer
outside the affected instruments' authorship chain must sign the audit report
at the exact reviewed commit. Owner approval of this ADR is necessary but does
not substitute for that external review. Until the signed report passes with no
critical blind spots, confirmatory campaigns remain unauthorized.
