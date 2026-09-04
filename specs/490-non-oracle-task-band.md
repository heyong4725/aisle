# SPEC 490 — Non-oracle reachable task-band calibration

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #346. This contract creates the
task instrument for the issue #347 causal study; it does not test the typed
dataflow claim. Confirmatory use depends on the issue #344 monolithic parity and
issue #345 statistics contracts. Pilot outcomes are task-selection data and can
never enter confirmatory tables.

The existing easy T1 evidence is near saturation at lower perception rungs,
while the current T2 expert baseline is 2/25. Neither is an acceptable frozen
causal instrument without a non-oracle perception audit, matched-interface
feasibility, and an outcome-independent held-out boundary.

## Candidate roles and non-oracle boundary

- BND-1: Calibration MUST fill exactly two task roles: `short_composition`, a
  short-horizon task solvable using installed registry capabilities; and
  `engineering`, a reachable but nontrivial task requiring an agent-authored or
  modified system component. Candidate families MAY begin from T1 at L2 and T2,
  respectively, but their final task ids, generator/config hashes, budgets, and
  selection evidence MUST be frozen by a human-reviewed amendment before any
  confirmatory session.
- BND-2: Every policy, planner, task-state machine, agent-authored component, and
  control artifact MUST consume only recorded sensor-derived perception at the
  declared rung. Simulator poses, segmentation masks at L2, object ids,
  attachment state, privileged scene metadata, and oracle-derived side channels
  MUST reach only frozen verifier/instrument processes. Validation MUST inspect
  direct and transitive graph routes, runtime environment, files, prompts,
  traces, error messages, and agent-visible tools; a clean YAML edge check alone
  is insufficient evidence of non-oracle operation.
- BND-3: Oracle verification MAY score a simulation episode only from a frozen,
  held-out process whose verdict, intermediate state, failure detail, and timing
  are unavailable to the policy during the attempt. Agent-visible feedback MUST
  use the same registered non-oracle failure interface in typed and monolithic
  arms. Every artifact MUST label policy perception and verifier evidence
  separately; an oracle verifier MUST NOT turn the policy path into `oracle` or
  validate perception portability.
- BND-4: Each candidate MUST declare the physical capability it abstracts,
  sensor inputs, action outputs, embodiment, workspace, timing and episode
  budget, success/failure semantics, permitted feedback, installed-capability
  set, agent edit authority, and excluded privileges. Undeclared dependencies,
  network access, cached target labels, seed-index lookups, or precomputed
  held-out answers invalidate the candidate.

## Independent perception instrument

- BND-5: Before task pilots, an independent CON-8 perception audit MUST replay a
  frozen corpus whose target/object truth is hidden from the perception process
  and exposed only to the audit scorer. It MUST report identity accuracy,
  localization position/orientation error where applicable, calibrated
  confidence, refusal/coverage, latency, and failure taxonomy by target class,
  pose/occlusion stratum, domain-randomization cell, seed, and sensor. Counts,
  uncertainty, raw predictions, frame/record hashes, model/config hashes, and
  calibration-versus-evaluation splits MUST be retained.
- BND-6: The perception envelope MUST freeze supported identity vocabulary,
  localization tolerances, confidence/refusal thresholds, camera and depth
  assumptions, lighting/texture/occlusion/pose ranges, synchronization, missing
  data behavior, and out-of-envelope refusal. Threshold selection MUST use only
  calibration records; evaluation records and task-pilot outcomes MUST NOT tune
  it. Missing truth, source records, strata, or denominators MUST fail the audit
  instead of being silently dropped.
- BND-7: A candidate is perception-eligible only if its pre-registered lower
  accuracy and upper error/latency criteria pass in every required stratum and
  refusal remains within its frozen availability limit. Aggregate success MUST
  NOT mask a failed target or randomization stratum. The eligibility criteria,
  independent unit, multiplicity handling, and sample/precision rule MUST be
  ratified before evaluation-corpus results are opened.

## Expert parity and unscored pilot selection

- BND-8: Each candidate MUST have typed and monolithic expert artifacts that
  pass the issue #344 structural parity manifest: identical task generator,
  sensors/perception records, policy-visible feedback, actuation primitives and
  gateway, verifier, reset, budgets, dependencies, authority, and evidence
  taps. Both experts MUST pass the same functional fixtures and the same
  pre-registered feasibility floor on paired development seeds. A paired
  equivalence interval for expert success and completion time MUST lie within
  frozen practical margins selected and powered under issue #345; failure of
  either artifact rejects the candidate rather than licensing arm-specific
  tuning.
- BND-9: Task-band pilots MUST use fresh agent sessions as the independent unit,
  fixed model/version/settings, matched prompts and budgets, frozen candidate
  artifacts, and development-only seeds. Each interface MUST randomize at least
  16 sessions per candidate unless a pre-ratified precision analysis requires
  more. The primary intent-to-treat denominator MUST retain every randomized
  session, including launch, timeout, and agent failures; only pre-registered
  treatment-integrity invalidations MAY be excluded, MUST remain reported, and
  MUST NOT be replaced. A candidate is band-eligible only when each interface
  has at least three successful and three unsuccessful randomized sessions and
  an observed intent-to-treat session-success rate from 0.20 through 0.80
  inclusive; all failures, exclusions, and uncertainty MUST remain visible.
- BND-10: The pilot selector MUST receive opaque interface labels and use a
  frozen deterministic rule. It MAY use perception eligibility, expert parity,
  pooled session success, completion/cost feasibility, and failure diversity;
  it MUST NOT inspect or optimize the typed-minus-monolithic contrast. Among
  eligible candidates for a role it MUST select the pooled success rate nearest
  0.50, then lower invalid-session rate, then lexicographically smaller content
  hash. If no candidate is eligible, the role remains open and a new
  human-reviewed candidate round is required; thresholds MUST NOT be relaxed
  after seeing results.
- BND-11: Pilot records MUST carry a permanent `unscored_pilot` evidence kind,
  campaign/session ids, agent/model/config hashes, candidate/interface opaque
  id, task/seed, budget ledger, exclusions, raw verifier and trace locations,
  and selection-input hash. Pilot seeds, sessions, agents, transcripts, learned
  artifacts, cached state, and tuned outputs MUST be disjoint from confirmatory
  inputs and MUST be rejected by confirmatory analyzers rather than pooled.

## Freeze, held-out bank, and task cards

- BND-12: Before confirmatory scoring, a machine-readable freeze manifest MUST
  commit the selected task and generator hashes, two expert/control artifacts,
  perception model/config/envelope, capability registry, graph or script,
  environment/lock/container, prompts, budgets, endpoints, exclusions/retries,
  randomization factors, analysis code/seed, and exact commands. It MUST name
  the protocol review and pilot-selection hashes and MUST refuse any later byte
  or parameter drift except through the documented deviation process.
- BND-13: Development, perception-calibration, perception-evaluation, pilot, and
  confirmatory held-out distributions MUST be disjoint by content identity, not
  merely seed integer. Each selected task artifact MUST commit at least 32
  distinct held-out seed records shared pairwise across interfaces unless the
  issue #345 power analysis, approved before scoring, requires another number.
  The manifest MUST freeze seed derivation, task factors, domain-randomization
  cells and ranges, exclusions, retry policy, and a salted commitment while
  withholding seed values/assets/outcomes from participants until execution.
- BND-14: A leakage audit MUST record filesystem, prompt, tool, network, process,
  environment-variable, cache, and IPC access boundaries and prove that agents
  and policy processes could not read held-out seeds, truth, oracle state, or
  outcomes before the permitted feedback point. Opening or changing a held-out
  artifact early invalidates affected sessions and triggers a documented
  deviation; replacing them silently or reusing pilot material is forbidden.
- BND-15: Each final artifact MUST include a versioned task card stating the
  physical robot capability exercised, why it matters, observation/action
  interfaces, excluded privileges, perception and verifier assumptions,
  portability and sim-to-real limits, known failure modes, expert parity result,
  pilot band evidence, held-out commitment and sample rule, hardware status,
  licenses, raw evidence locations, and one-command regeneration/validation.
  Simulation capability MUST NOT be relabeled as physical validation.
- BND-16: A CON-8 auditor MUST regenerate perception, expert-parity, pilot-band,
  selection, split-disjointness, freeze, and task-card reports from named raw
  records. It MUST return `ok: false` on missing/duplicate/cross-split ids,
  unrecognized evidence kinds, arm-specific budgets or privileges, fewer than
  the frozen held-out count, unhashed inputs, post-freeze drift, or tables not
  reproducible byte-for-byte. Human-authored summary numbers are not evidence.

## Required fixtures and limitations

Fixtures include direct and transitive oracle leaks, oracle-derived files and
feedback, missing perception strata, threshold tuning on evaluation records,
expert capability/budget mismatch, saturated and floor pilots, opaque-label
swaps, selection ties, pilot/held-out overlap under different seeds, early
held-out disclosure, post-freeze drift, and regenerated-table hash changes.
Passing this contract establishes a calibrated simulation task instrument, not
the typed treatment effect or physical-robot performance.
