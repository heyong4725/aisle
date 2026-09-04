# ADR-46 — Session-level confirmatory statistics contract

Status: PROPOSED — requires owner approval and independent statistical review
before protocol freeze. Date: 2026-08-31. Issue: #345.

## Decision

AISLE will analyze autonomous coding sessions as independent experimental
units. Held-out seeds, episodes, events, and safety exposures remain nested
observations. The common analyzer will use Clopper-Pearson exact binomial
intervals for artifact and arm rates, Newcombe score intervals for binary
session-level risk differences, and treatment-stratified seeded session
bootstrap intervals for continuous or nested outcomes. Pre-declared
agent-system strata are reported beside the pooled treatment effect. The
analyzer will retain exclusions and censoring, produce Kaplan-Meier
time-to-acceptance summaries, require pre-declared equivalence/non-inferiority
margins, and report exact one-sided zero-event bounds. Binary and continuous
power calculations expose their assumptions and sensitivity ranges; pilot data
may inform those assumptions but confirmatory outcomes may not revise them.

This chooses a transparent, fixture-testable frequentist surface over a more
flexible hierarchical model for benchmark v1. The tradeoff is coarse
uncertainty with small numbers of sessions and dependence on declared power
assumptions. Raw session points, achieved sample sizes, sensitivity tables, and
negative/null results therefore remain visible. A later hierarchical model is
permitted only through a new human-ratified protocol version, never as a
post-result substitution.

## Gate

SPEC 400 is implemented tests-first after this spec-change merges. Before a
confirmatory campaign, an independent statistical reviewer must recompute the
synthetic fixtures, inspect the power and interval methods, and sign a retained
review report. Owner approval of this ADR does not substitute for that review.
