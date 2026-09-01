# SPEC 400 — Confirmatory benchmark statistics

Status: PROPOSED, HUMAN-REVIEW GATED. This specification does not authorize a
confirmatory run. It becomes the statistical contract for issues #347 and #349
only after the review record required by STA-12 is accepted. Depends on: CON-5,
CON-8, CON-12, HAR-4, and the treatment-integrity contract from issue #353.

The coding-agent **session** is the experimental unit. Artifacts, held-out
seeds, episodes, events, and safety exposures are nested observations unless a
later human-ratified protocol names a different unit and justifies it. Pilot
records may calibrate assumptions but never enter a confirmatory estimate.

## Frozen protocol and retained input

- STA-1: Before scored collection, each confirmatory campaign MUST provide a
  machine-readable protocol declaring the primary estimand, experimental unit,
  treatment arms, outcome and direction, smallest effect of interest, alpha,
  target power, allocation, stopping rule, inclusion/exclusion rules, analysis
  seed, and an equivalence or non-inferiority margin (or an explicit
  `not_applicable` rationale). The statistics CLI MUST refuse an unresolved or
  internally inconsistent protocol.
- STA-2: The analyzer input MUST retain every randomized assignment and started
  session, including never-started assignments and infrastructure exclusions,
  with a stable session id, treatment, agent-system identity, temporal block,
  lifecycle/inclusion status and reason, budget/censoring data, session outcome,
  cost fields, and nested artifact/seed observations. An excluded session MUST
  remain in the session-flow output and MUST NOT silently enter an effect
  estimate.
- STA-3: Pilot and confirmatory records MUST carry distinct immutable campaign
  identities. An analyzer invocation MUST refuse mixed identities, pilot rows
  in a confirmatory analysis, duplicate session ids, post-stopping-rule rows,
  or records whose treatment/outcome schema differs from the frozen protocol.

## Analysis surface

- STA-4: `harness stats analyze --protocol P --records R` and `harness stats
  power --protocol P` MUST obey CON-8 and derive one JSON result from the named
  machine-readable inputs. The result MUST include schema version, input
  hashes, protocol identity, analysis seed, assumptions, warnings, and all
  values used by generated tables or figures; hand-entered result fields are
  not an evidence source.
- STA-5: Power analysis MUST support (a) binary session success from declared
  control/treatment probabilities and the smallest risk difference and (b)
  continuous session-level cost from a declared standard deviation and
  smallest mean difference. It MUST report the method, per-arm sample size,
  achieved power under the declared assumptions, and a sensitivity table. It
  MUST NOT estimate confirmatory stopping rules from confirmatory outcomes.
- STA-6: Per-artifact binary outcomes MUST report Clopper-Pearson exact
  binomial intervals with sidedness and confidence level explicit. The
  one-sided 95% lower bound for 8/8 (approximately 0.688) MUST be below 0.90,
  and the analyzer MUST reject any claim that this cell establishes a
  population success rate above 0.90.
- STA-7: Treatment estimates MUST use sessions, not episodes/events, as
  independent observations. Binary session-success effects MUST report arm
  rates with exact intervals and a Newcombe score risk-difference interval;
  pre-declared agent-system strata MUST be reported separately beside the
  pooled effect. Continuous or nested outcomes MUST aggregate within session
  and use a treatment-stratified, seeded session bootstrap (or a human-ratified
  hierarchical model). Resampling nested events as independent treatment
  replicates is forbidden.
- STA-8: Equivalence and non-inferiority decisions MUST use the frozen margin,
  direction, alpha, and a compatible confidence interval: two-sided
  `1 - 2*alpha` for equivalence and one-sided `1 - alpha` for non-inferiority.
  `no significant difference` MUST NOT produce an equivalence decision. The
  result MUST expose the margin, interval, decision rule, and decision.
- STA-9: Time-to-first-accepted-system summaries MUST retain right-censored
  sessions and report the at-risk/event/censor table plus Kaplan-Meier survival
  points. Sessions without acceptance MUST NOT be dropped or assigned an
  invented event time.
- STA-10: Zero-event claims MUST report the Clopper-Pearson exact one-sided
  upper binomial bound, confidence level, event count, and exposure denominator.
  Missing or mixed exposure units MUST fail closed rather than yield a bound.
- STA-11: Every analysis MUST emit a session-flow table containing randomized,
  started, completed, included, infrastructure-excluded, censored, and analyzed
  counts by arm, with reasons; count reconciliation failures MUST make the CLI
  return `ok: false`.

## Freeze and review gate

- STA-12: Before any confirmatory session starts, an independent statistical
  reviewer who did not author the analyzer MUST review the protocol, power
  assumptions, interval/decision methods, synthetic fixtures, and known
  limitations. The signed review record, reviewer role, disposition of every
  finding, frozen artifact hashes, and external timestamp MUST be retained.
  Missing or unresolved review blocks protocol freeze and confirmatory data
  collection.

## Required fixtures and limitations

Synthetic acceptance coverage includes clustered observations, all-success,
all-failure, right censoring, infrastructure exclusions, duplicate/mixed input,
equivalence and non-inferiority boundaries, zero events, deterministic seeded
bootstrap, and the 8/8 counterexample. Fixture values are independently
computed rather than copied from analyzer output.

The analyzer cannot repair a biased task, treatment leakage, informative
infrastructure failure, model-service drift, or an implausible power
assumption. Small-session bootstrap intervals may be coarse; raw session points
and sensitivity results remain mandatory. Statistical review is a gate, not a
claim that the design is free of limitations.
