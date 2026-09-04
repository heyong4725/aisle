# ADR-59 — Reproduction starts outside the campaign machine

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #355.

## Context

AISLE attests frozen code and environments and preserves some agent-authored
deliverables in Git refs. That proves useful identity properties, but the
current repository has no complete confirmatory evidence package, independent
operator record, second-machine result, DOI deposit or double-blind bundle.
`runs/` is gitignored, and a campaign deliverable ref deliberately excludes its
large trace evidence. Attestation and maintainer CI therefore cannot satisfy
the review finding that another operator can reproduce the primary evidence.

## Decision

The release unit is a content-addressed evidence bundle, not a repository tag
or summarized table. Every primary output must have a complete manifest closure
over raw records, protocols, executed artifacts, environments, analyzers,
failures, exclusions and deviations. A single fail-closed command validates
that closure and regenerates the reported tables and figures from a clean
directory without original gitignored state.

Independence is procedural: a different operator and machine begin from the
public or double-blind bundle, with cell selection and allowed assistance frozen
before original outcomes are disclosed. The resulting report evaluates CON-5's
bit-exact, cadence, physics-tolerance and statistical layers separately and
retains mismatches and failed gates. Lockstep wall-time cost is a measured,
host-scoped result rather than a portable assertion.

The public deposit and double-blind artifact are two deterministic projections
of one source manifest. Redaction is explicit and cannot erase adverse evidence.
A DOI passes only after a real immutable, downloadable deposit round-trips with
matching checksums and regenerated outputs. Until confirmatory inputs, an
independent participant and deposit authority exist, status remains pending.

## Gate

SPEC 530 is implemented tests-first after this spec-change. Packaging fixtures,
validators and synthetic round trips establish preparedness only. #355 remains
open until the independent execution and real archive audit satisfy every
acceptance criterion; #357 separately owns participant-facing benchmark v1 and
its blind evaluation service.
