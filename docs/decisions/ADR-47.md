# ADR-47 — Canonical claim catalog and four-zone benchmark narrative

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #358.

## Decision

AISLE will keep one machine-readable claim catalog at
`docs/claim-evidence.yaml` and generate the human matrix from it. The catalog is
canonical for a claim's status, scope, evidence, uncertainty, limitations, and
allowed wording; README remains the sole canonical dated project-status page.
Headline prose points back to stable claim ids, while evidence paths and marker
coverage are checked in CI. This preserves readable narrative without allowing
the same claim to acquire incompatible status in multiple documents.

The external architecture will use four trust zones: (1) the mutable
participant/agent surface, (2) the frozen evaluator, (3) the scoped trusted
actuation boundary, and (4) the hidden evaluation controller. The current
topology validator proves only declared graph-path gating. Until issue #350
ratifies and tests a wider process boundary, the narrative will not call the
guard unbypassable or claim arbitrary agent code cannot reach actuation through
an unmodeled side channel.

The broad technical report remains the complete historical and systems record.
The focused benchmark paper is limited to the typed-versus-monolithic causal
study, typed-evidence fault study, scoped safety boundary, and independent
reproduction. A matrix row can be valid in the report while explicitly outside
the focused paper.

## Gate

Implementation follows tests-first after this spec-change merges. Catalog
population is an audit task: unsupported wording is narrowed, not promoted, and
missing empirical evidence remains missing. An external terminology reviewer
must sign the CLM-12 record before public release; owner approval of this ADR is
not that review.
