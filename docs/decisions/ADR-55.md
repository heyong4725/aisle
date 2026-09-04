# ADR-55 — Select two non-oracle tasks by blinded unscored pilots

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #346.

## Context

Easy T1 evidence is near saturation at lower perception rungs, while the current
T2 expert baseline is 2/25. T3 has exceeded practical session budgets. Freezing
any of those conditions as-is risks a ceiling, a floor, or an oracle-perception
instrument that cannot test an engineering workflow.

## Decision

AISLE will fill two roles rather than bless an existing tier by name:

1. `short_composition` starts from a short L2, installed-capability candidate.
2. `engineering` starts from a T2-derived candidate that requires a system
   modification but remains feasible for matched typed and monolithic experts.

The final candidates are not selected by this ADR. First, an independent
perception audit fixes the sensor-derived accuracy/refusal envelope. Matched
typed and monolithic experts then pass one structural, functional, and powered
equivalence gate. Fresh-session development pilots must show successes and
failures in both interfaces under the intended budget. A deterministic selector
receives opaque interface labels, ignores the treatment contrast, and selects
the eligible candidate with pooled success nearest one half. Pilot data is
permanently marked unscored and cannot enter confirmatory analysis.

After selection, a human-reviewed freeze amendment names the task, generator,
perception, experts, budgets, randomization, exclusions, analysis, and at least
32 committed held-out seeds per task unless the pre-scoring power analysis
requires another count. Participants see neither held-out values nor privileged
truth. Oracle state may remain inside the frozen scorer but cannot reach policy,
feedback, tools, caches, or prompts.

Each selected task gets a task card explaining the physical capability,
observation/action boundary, excluded privilege, perception envelope,
portability limits, parity and pilot evidence, held-out commitment, raw records,
and regeneration command. The card and evidence remain simulation-labeled until
real hardware is executed.

## Alternatives rejected

- Freeze T1/L0 or L1 because it is reliable: the result risks saturation and
  does not exercise the intended non-oracle perception path.
- Freeze stock T2 because it is difficult: 2/25 is currently a floor, not a
  useful middle band.
- Choose the task with the largest typed-minus-monolithic pilot difference:
  that bakes the target result into the instrument.
- Publish held-out seeds for transparency before sessions: commitments and
  post-campaign release preserve auditability without enabling leakage.
- Count episodes or retries as independent pilot samples: the coding-agent
  session is the experimental unit for band calibration.

## Gate

SPEC 490 is implemented tests-first after this spec-change and the #344/#345
contracts merge. Human approval does not select either final task. No
confirmatory session may start until raw pilot evidence passes the auditor and a
reviewed freeze amendment records immutable artifacts and held-out commitments.
