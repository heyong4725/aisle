# A3 findings: params-only vs params+code on T1 (pin 8af9b47a, 2026-08-14)

ADR-a3-protocol (ratified via #187), runner #234: two sequential
isolated T1 sessions, arm F on the standard research contract, arm P
with the params-only rule committed on its worktree (treatment diff
sha256 in the record). Both arms: desk-T1 budgets (0.4M / 2.5 h),
identical prompt/seeds, held-out scoring 100..107.

## Result: the constrained arm won on efficiency at equal quality

| | arm F (params+code) | arm P (params-only) |
|---|---|---|
| first verified success | 13.8 min | **9.8 min** |
| session tokens | 395,676 (99% of budget) | **200,033 (50%)** |
| session wall | 85 min | **24 min** |
| dev rollouts to deliverable | 4 | **1** |
| holdout pass@1 / pass@8 | 1.0 / 1.0 | 1.0 / 1.0 |
| params_leak audit | n/a (F may author) | **clean — full compliance** |
| wrong_object | 0 | 0 |

Arm F used its code-authoring freedom to write and register a skill
(`skills/ik-transfer-v2/`) the task did not require, spending ~2× the
tokens and ~3.6× the wall for an identical held-out score. On T1, the
registry's existing capability set is sufficient, and the CONSTRAINED
action space acted as a prior that kept the agent on the efficient
path — the §10.2 "schema tax" worry inverted: at this tier the schema
is a subsidy.

## Caveats (stated before anyone quotes the table)

- **n=1 per arm** at one budget on the EASIEST tier with a measured
  1.0 expert baseline; the design predicts code authorship matters at
  T2/T3, where capability gaps are deliberate (desk-H3: both-arm
  failures there). A T2 params-only arm would likely be FLOORED — the
  fair reading is "params-only is cheaper where the registry already
  covers the task," not "code authorship is useless."
- Arm P's pin-filtered `first_success_wall_s` is null by a metrics
  artifact: its committed contract variant moves the worktree sha, so
  campaign_metrics' strict sha==pin admissibility rejects its rollouts
  (the desk-H3 "agent-only pin descendants ARE the treatment" ancestry
  rule applies conceptually; the unfiltered trajectory in the record
  supplies the 9.8 min figure above and both arms' single-sha rollout
  sets make it unambiguous). Analyzer follow-up shared with the desk
  campaign's flag-semantics items.
- H5 held: wrong-medicine 0 in both arms; arm F's authored skill went
  through the registry path (eval.yaml present), not around it.
