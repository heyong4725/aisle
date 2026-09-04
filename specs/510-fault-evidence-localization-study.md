# SPEC 510 — Typed evidence versus logs-only fault localization

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #349. This contract pre-registers
the replicated live-fault comparison but authorizes no scored collection. It
depends on passing and freezing issue #345 statistics, issue #348 sealed fault
bank, issue #353 treatment integrity, and issue #354 instrument-audit gates.
Results update issue #358 regardless of direction. Historical H6 records are
unblinded motivation only and MUST NOT enter calibration, power, or estimates.

The treatment is a bundled diagnostic evidence interface over the same running
typed robot system and repair authority. It does not compare the typed-dataflow
runtime to a monolithic runtime or isolate one trace/probe feature.

## Evidence arms and parity

- FEL-1: The two conditions MUST be `typed_evidence` and `logs_only`. Both MUST
  use the same frozen base process-log producers, fields, filters and retention;
  episode-outcome schema; task description; component source/edit surface;
  repair submission API; budgets; environment; paired fault assignment;
  evaluator; and authority. Session log bytes may diverge after agent actions
  and MUST NOT be asserted identical. A versioned treatment table MUST enumerate
  every visible file, field, command, diagnostic, and help surface with hashes
  and classify it as common or the declared treatment difference; an undeclared
  difference blocks collection.
- FEL-2: `typed_evidence` MUST add only the frozen bundle of episode failure
  taxonomy, typed topic traces/probes, capability and topic-schema manifests,
  structured guard/intervention statistics, graph topology/validation, and
  validated graph operations. The protocol MUST name exact commands, outputs,
  retention, rate/credit limits, and hashes. Structured tools MUST expose live
  system evidence and ordinary validation results, never hidden fault identity,
  injector state, truth, or accepted repair.
- FEL-3: `logs_only` MUST expose conventional timestamped text logs and the
  common outcome stream but deny typed probe/trace APIs, schema/capability
  manifests, graph topology, structured validation diagnostics, guard summary
  APIs, and generated equivalents. It MAY use ordinary source inspection,
  search, edit, language/import/runtime errors, and the common repair broker.
  Removing typed evidence MUST NOT remove a declared common base observation,
  repair primitive, component namespace, or budget available in the other arm.
- FEL-4: Both arms MUST act through one evaluator-owned repair broker supporting
  the same component files, patch size/classes, validate/test/relaunch or
  quiescent-swap operations, retry count, and driver/guard boundary. The broker
  MUST record semantically equivalent actions and refuse hidden references,
  direct controller/injector/evaluator access, guard bypass, fault-menu reads,
  or arm-specific repair shortcuts. Repair authority parity MUST pass synthetic
  correct/incorrect/novel-code fixtures before the fault bank opens.

## Diagnosis, repair, and outcomes

- FEL-5: Before the first edit, patch, restart, swap, or repair command, the
  agent MUST submit exactly one controller-timestamped diagnosis containing
  `fault_present`, an opaque canonical component id set, confidence, and cited
  participant-visible evidence ids. The primary diagnosis is immutable. A
  missing/late submission is incorrect; for coupled faults the exact full
  component set is required, while partial/set-distance scores remain
  descriptive. A sham is correct only when `fault_present: false` and the
  component set is empty.
- FEL-6: Co-primary outcome A MUST be correct pre-repair component localization
  on fault sessions. Co-primary outcome B MUST be an accepted repair with
  held-out recovery on fault sessions. Each is a binary session outcome with
  risk difference `typed_evidence - logs_only`, exact arm intervals, Newcombe
  interval, frozen superiority rule, smallest effect of interest, alpha and
  multiplicity rule. The claim is supported only by the pre-registered joint
  co-primary decision; success on one endpoint cannot substitute for the other.
  Null, negative, imprecise, and logs-favoring outcomes are valid and MUST map
  to the frozen issue #358 `weakened`, `rejected`, or `undecidable` status.
- FEL-7: An accepted repair MUST pass frozen public syntax/interface/safety
  checks and an evaluator-private held-out recovery suite for the assigned fault
  plus clean-regression tasks. The protocol MUST freeze task/seed records,
  recovery and quality thresholds, minimum fault-removal effect, clean
  non-inferiority margin, safety rule, retry/credit window, and artifact
  selection before scoring. The agent terminates before held-out execution and
  receives no hidden outcome that could enable best-of-N repair selection.
- FEL-8: Post-repair evidence MUST report per-artifact held-out successes,
  failures, exclusions, exact uncertainty, task/seed denominators, and fault and
  clean quality. Task/seed observations are nested within the session for the
  treatment effect. A repair that disables the task, masks evidence, removes the
  evaluator, bypasses the guard, or merely suppresses activation MUST fail even
  if a short visible window looks healthy.
- FEL-9: Secondary session outcomes MUST include fault-family and component-set
  accuracy; diagnosis confusion matrix; sham false alarms and unnecessary
  mutation/restart; partial coupled localization; time to diagnosis and accepted
  repair with censoring; input/output/total tokens; evidence queries/log reads;
  edit/patch size; validation, relaunch and swap counts; regressions;
  post-repair quality; guard/authorization interventions; collisions and safety
  events; and every exclusion. Units, direction, aggregation, and exploratory
  status MUST be frozen before scoring.

## Fault allocation, shams, replication, and power

- FEL-10: Scored assignments MUST come only from one sealed issue #348 bank that
  has passed diversity, efficacy, safety, sham indistinguishability, novel-
  repair, commitment, and replay gates. The allocation MUST ensure each arm and
  agent system receives fault and sham cells across the frozen required
  families, persistent/intermittent modes, coupled faults, and at least one
  novel-code repair instance. Public H6 instances, revealed faults, or operator-
  selected cells are inadmissible.
- FEL-11: At least two independently supplied coding-agent systems MUST run
  fresh isolated sessions. The campaign controller MUST allocate opaque paired
  fault/sham instances between evidence arms in concealed temporal blocks within
  agent-system × fault-family/control strata, recording arm order, host/resource
  pool, planned start, and load baseline. Same-experiment transcripts,
  diagnoses, repairs, results, caches, or revealed assignments MUST not reach a
  later session.
- FEL-12: The issue #345 power analysis MUST determine numbers of independent
  sessions, fault families/instances, sham cells, and assignments per evidence
  condition from independently justified assumptions or excluded pilots using
  public synthetic canary faults, both smallest effects, alpha/multiplicity,
  target power, allocation, and attrition sensitivity. Sealed scored instances
  and historical H6 outcomes MUST NOT supply the assumptions. The larger co-
  primary requirement governs. Sample size, complete block list, sham ratio,
  stopping rule, and family weights MUST be frozen with no optional stopping,
  post-result family substitution, or adaptive severity.
- FEL-13: Sham controls MUST traverse the same sealed selection, staging,
  activation marker, timing, participant view, evidence capture, diagnosis,
  broker, retention, and analysis paths. The controller MUST define a common
  session time origin without exposing sham status. Sham false-alarm rate is the
  proportion of sham sessions with `fault_present: true`; unnecessary-action
  rate is the proportion with any edit/restart/swap/repair request. Both MUST be
  reported by arm and agent system with exact uncertainty and a frozen
  non-inferiority or equivalence rule if used for a claim.

## Sealing, integrity, and lifecycle

- FEL-14: Participant views MUST exclude the production bank, fault menu,
  injector and assignment ledgers, environment triggers, clean/healthy
  references or diffs, repair oracle, reveal material, private controller paths,
  and git objects/caches containing them. Issue #353 confinement, canary scans,
  access logs, treatment tuples, and postflight MUST pass for both evidence
  arms. A private fingerprint or side-channel hit is infrastructure-invalid and
  blocks affected campaign use rather than counting as agent localization.
- FEL-15: The retained session ledger MUST include every planned, randomized,
  staged, activated, launched, diagnosed, mutated, submitted, held-out tested,
  censored, failed, infrastructure-invalid, and excluded session with block,
  arm, agent, opaque assignment, timing, reason, budget, treatment/fault
  commitment, evidence hashes, and linked rerun. Agent-caused tool/runtime/
  repair failures are outcomes; arm-neutral infrastructure exclusions follow a
  pre-registered independent classification and remain in flow and bounding
  sensitivity analyses without silent replacement.
- FEL-16: Historical H6 records MUST carry an explicit `historical_unblinded`
  evidence kind and remain absent from bank calibration, power inputs,
  randomization, analyzer records, denominators, confusion matrices, and
  treatment estimates. Reports MAY cite 3/3 only beside its one-session-per-
  public-fault, no-comparator, no-sham, source-readable, unattested limitations.

## Instrument audit, freeze, analysis, and release

- FEL-17: Before scored collection, issue #354 MUST independently audit every
  decision-bearing diagnosis parser, truth mapping, repair/held-out scorer,
  clean-regression and safety rule, exclusion/rerun classifier, timer/credit
  window, evidence-arm filter, leakage detector, statistical analyzer,
  confusion matrix, and table/figure transform. Any critical surviving,
  wrong-layer, wrong-verdict, not-executed mutation, harmful false alarm, or
  unresolved independent-review finding MUST keep the campaign locked.
- FEL-18: A content-addressed freeze manifest MUST bind hypotheses, co-primary
  and secondary endpoints, margins/power/sample/blocks, stopping/exclusions/
  reruns/deviations, agent systems, evidence treatment table and filters, repair
  broker, sealed-bank commitment and allocation algorithm, held-out suite,
  confinement, prompts/budgets/authority, environment, evidence schemas,
  instruments/audit/review, analyzer/fixtures/report templates, and exact
  commands. It MUST be independently reviewed and externally timestamped before
  scoring; behavior-affecting changes start a new campaign or documented
  separate deviation, never a silent protocol revision.
- FEL-19: A registered CON-8 analyzer MUST regenerate one machine-readable
  result, session-flow diagram, arm/agent/family tables, co-primary effects and
  decisions, sham rates, full localization confusion matrix including
  `no_fault` and coupled sets, held-out intervals, censor tables, exclusion
  bounds, and raw session-point export. It MUST fail on missing assignments or
  evidence, duplicate/mixed campaign ids, pilot/historical rows, block/truth
  mismatch, unsealed or unreplayed faults, nested-unit inflation, post-stopping
  rows, changed protocol/instrument hash, or non-reconciling derived output.
- FEL-20: Release evidence MUST retain the timestamped protocol, prerequisite
  gates, bank commitment and post-close reveal/replay, treatment table,
  randomization/assignment ledger, all private and participant session records,
  full transcripts/tool/budget/access logs, diagnoses, patches and submitted
  repairs, held-out raw results, instrument-audit predecessors/review, analyzer,
  tables/figures, deviations, claim disposition, and exact regeneration command.
  Credential/personal-data redaction MUST be deterministic and logged; audited
  full transcripts remain in the controlled archive.

## Required fixtures and limitations

Fixtures cover arm-surface leakage and missing common evidence, late/revised
diagnoses, exact/partial coupled sets, sham false alarms, unnecessary mutation,
novel-code repair, activation suppression, clean regression, unsafe repair,
censoring, imbalanced exclusions, contamination, block imbalance, historical
row injection, mutation blind spots, null/negative/logs-favoring effects, and
raw-input hash changes. This study estimates the bundled evidence-interface
effect for the sealed bank and agent systems; it cannot prove completeness of
the fault universe, isolate a single evidence feature, or establish hardware
fault-localization performance.
