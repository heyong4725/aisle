# ADR-60 — Benchmark v1 is a versioned trust boundary

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #357.

## Context

AISLE currently exposes capable research machinery: a contributor quickstart,
typed graphs and registry, Claude/Codex campaign runners, expert artifacts and
rich evidence capture. It does not yet expose a stable benchmark version,
monolithic reference treatment, public/private distribution split, blind
evaluator, submission and leaderboard contract, hidden-set rotation policy,
complete release licensing/citation metadata or external-user completion.
Calling the research repository a released benchmark would hide those gaps.

## Decision

Benchmark v1 is one content-addressed trust boundary spanning participant tools,
both interface treatments, tasks, hidden-bank commitments, scorer, safety and
authorization, budgets, baselines, submission validation, analysis and
governance. At least two coding-agent systems run both treatments. Public local
development uses the same schemas but is permanently labeled non-blind.

Private truth and faults live only in an evaluator controller outside participant
authority. A canary-backed audit covers filesystem, process, environment,
package, network, timing and artifact channels. Submissions carry full treatment,
provenance, resource and evidence records; reports preserve uncertainty, safety,
failures and cost rather than reducing the benchmark to one success score.

Any change to a validity-bearing surface creates a new benchmark version. Hidden
set exposure is contamination, not a harmless documentation event, and follows
a committed rotation policy. Public release requires complete licensing and
citation closure plus the #355 archive link. An actual outside user must finish
from the released artifact without bespoke maintainer repair before #357 closes.

## Gate

SPEC 540 is implemented tests-first after this spec-change. Internal fixtures
and clean-clone rehearsals establish preparedness only; the blind-release and
external-user criteria remain pending until independently observed. A null or
negative typed-dataflow study affects claim status, not benchmark validity.
