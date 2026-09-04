# ADR-56 — Use a replicated block-randomized session trial for H4

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #347.

## Decision

AISLE's primary causal study will randomize fresh coding-agent sessions between
the equal-capability typed-dataflow and monolithic surfaces. The session is the
experimental unit. The primary estimand is the session-success risk difference:
whether one deadline-selected deliverable launches and passes a hidden paired
held-out task acceptance function within fixed resource and authority budgets.

The design crosses at least two independently supplied coding-agent systems
with both selected non-oracle task roles. Allocation is balanced in concealed
temporal blocks within agent × task strata, with at least ten assignments per
arm per stratum and a larger sample whenever the frozen power analysis requires
it. Every stratum is reported before a pre-registered pooled estimate. Episodes,
seeds, retries, edits, and tool calls remain nested observations.

All #344 parity, #345 statistics/review, #346 task-band/freeze, and #353 sealed-
treatment gates must pass at recorded hashes. Sessions are built independently;
no later session can see same-experiment deliverables or findings. Every
assignment and exclusion remains in the flow record, treatment-surface failures
remain outcomes, and external infrastructure exclusions receive pre-registered
bounding sensitivities rather than silent replacement.

The protocol, analyzer, randomization, hypotheses, endpoints, margins, sample
size, stopping/exclusion rules, tasks, agents, prompts, budgets, authority, and
evidence schemas are content-addressed, independently reviewed, and externally
timestamped before scoring. Behavior-changing revisions start a new campaign or
a documented separate deviation. Null, negative, imprecise, and monolithic-
favoring results are complete outcomes, not reasons to redesign the claim.

## Interpretation decisions

- The treatment is the bundled engineering interface; this trial does not
  isolate types, registry hints, validator diagnostics, or runtime separately.
- Cost results are not called `at equal quality` unless pre-registered
  equivalence passes for acceptance probability and accepted-artifact quality.
- Artifact legibility is optional and secondary. If retained, raters are
  blinded to agent, outcomes, order, and hypothesis; unavoidable interface
  recognizability is measured and reported.
- Simulation tasks establish no physical-robot effect.

## Alternatives rejected

- Historical or expert-only controls: they do not randomize autonomous sessions.
- Episode-level sample sizes: episodes are nested within agent sessions.
- Best-of-many deliverables: it changes the primary success opportunity.
- Sequentially complete one arm: temporal/model-service drift confounds it.
- Drop infrastructure failures without sensitivity bounds: exclusions can be
  treatment-associated and directionally bias the result.

## Gate

SPEC 500 is implemented tests-first only after this spec-change and all named
prerequisites merge. Human approval creates no causal evidence. Confirmatory
collection remains locked until the complete freeze manifest and independent
review exist, and #347 remains open until raw sessions regenerate every result.
