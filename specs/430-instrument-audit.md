# SPEC 430 — Evaluation-instrument audit and mutation benchmark

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #354. This contract does not
assert that any current instrument is independently validated and does not
authorize confirmatory collection. It depends on the frozen statistical
protocol from issue #345, the claim/evidence registry from issue #358, and the
treatment-integrity checks from issue #353.

An **instrument** is any verifier, scorer, validator, analyzer, exclusion or
stopping rule, provenance check, safety-exposure counter, leakage detector, or
figure/table transform whose output can affect a primary-paper result. A
mutation is detected only when the frozen expected layer produces the frozen
oracle outcome; a crash, unrelated refusal, or downstream discrepancy is not
automatically a pass.

## Audited inventory and coverage

- AUD-1: Before an audit runs, a machine-readable instrument inventory MUST
  identify every primary-paper estimand and exclusion rule from the frozen
  protocols and every decision-bearing transform on their path from raw record
  to published table or figure. Each entry MUST name its stable id, source
  schema and fields, implementation path and callable/CLI, output fields,
  instrument hash, upstream/downstream ids, and responsible authorship record.
  An unresolvable or uncovered primary entry MUST make the inventory invalid.
- AUD-2: The inventory MUST cover verifier/scorer boundary decisions, validator
  false accepts and false rejects, analyzer arithmetic and provenance,
  inclusion/exclusion and infrastructure-rerun rules, stopping and credit
  windows, safety-exposure accounting, hidden-test leakage and treatment
  contamination, and every primary figure/table derivation. A category may be
  marked `not_applicable` only with a reviewable protocol citation and reason.
- AUD-3: A versioned machine-readable mutation catalog MUST map at least one
  valid mutation to every primary estimand, every exclusion rule, and every
  inventory category in AUD-2. Each mutation MUST declare a stable id, target
  instrument and coverage ids, mutation family and exact operator, fixture,
  severity and rationale, expected detection layer, test oracle, and the
  evidence paths to retain. Coverage MUST be checked mechanically rather than
  inferred from mutation names or prose.

## Mutation execution and independent oracles

- AUD-4: The audit runner MUST obey CON-8 and apply one catalogued mutation at
  a time to a disposable copy of a content-addressed pristine fixture or
  instrument. It MUST refuse dirty, hash-mismatched, unknown, duplicate, or
  multi-mutation inputs and MUST never modify preserved raw observations or the
  repository worktree. The same frozen command and environment MUST run the
  pristine control and its mutated case.
- AUD-5: Every catalog entry MUST have an oracle fixed before execution. The
  oracle MUST state the expected layer, status/error or recomputed value, and
  comparison rule including tolerances. It MUST be hand-derived or produced by
  an implementation independent of the affected instrument; copying an
  expected value from the system under test, accepting any nonzero exit, or
  treating detection at an unexpected layer as success is forbidden.
- AUD-6: Every primary analyzer MUST have fixture-based recomputation tests
  whose reference implementation shares no production calculation helpers or
  decision constants with that analyzer. Fixtures MUST include boundary values
  and independently documented expected intermediate and final values for the
  primary estimate, uncertainty result, and all applicable exclusions. Shared
  parsing/schema code is permitted only when malformed-input behavior is not
  the property being recomputed.
- AUD-7: The catalog MUST include directionally paired and boundary mutations
  where applicable: false accept/false reject, include/exclude, early/late
  credit, under/over-counted exposure, leaked/clean treatment, altered raw
  value/provenance, and changed table/figure input. It MUST also include
  pristine and semantics-preserving negative controls that must not trigger a
  detector.

## Result accounting and blocking gate

- AUD-8: One deterministic JSON audit report MUST retain the inventory,
  catalog, fixture, instrument, runner, protocol, and environment hashes; the
  command and seed; and one result for every catalog mutation and negative
  control. Results MUST distinguish `detected_as_expected`,
  `detected_wrong_layer`, `wrong_verdict`, `survived`, `false_alarm`, and
  `not_executed`, with actual detector output and evidence paths. Missing or
  not-executed cases remain visible and MUST NOT improve a rate.
- AUD-9: The report MUST compute the detection rate as
  `detected_as_expected / all catalogued mutations` and the false-alarm rate as
  `triggered negative controls / all negative controls`, with integer
  numerators and denominators. It MUST list every surviving, wrong-layer,
  wrong-verdict, and not-executed case as a blind spot, broken down by primary
  estimand, exclusion rule, category, severity, and detection layer. No case
  may be silently dropped or reclassified after execution.
- AUD-10: A mutation is critical when it can alter a primary estimate,
  inclusion/exclusion decision, stopping/credit decision, safety exposure,
  leakage/contamination verdict, or published primary value without the frozen
  oracle response. Any critical case not classified `detected_as_expected`, any
  false alarm that can exclude an admissible session, or incomplete AUD-1/AUD-3
  coverage MUST set `publication_gate: blocked` and MUST block protocol freeze,
  confirmatory collection, and publication until repaired and re-audited.
- AUD-11: The gate decision MUST be generated from the per-case results and
  frozen severity rules. Hand-edited summaries, an aggregate rate alone, or an
  unresolved reviewer finding MUST NOT override a blocked result. Repairs MUST
  create a new instrument hash and complete audit run while preserving the
  failed predecessor.

## Independent review, freeze, and release

- AUD-12: Before the audit can pass, a human or external contributor who did
  not author any affected instrument MUST review the inventory, catalog,
  independent recomputations, raw audit outputs, blind spots, and gate result.
  The retained review record MUST identify the reviewer and reviewed commit,
  show the affected-file authorship check, link an externally timestamped
  signature or approval, enumerate every finding and disposition, and declare
  pass or block. Missing independence evidence, signature, or unresolved
  finding MUST keep the audit blocked; repository-owner approval of this spec
  does not satisfy this requirement.
- AUD-13: Protocol freeze MUST record immutable hashes for the approved
  inventory, mutation catalog, fixtures, reference recomputations, audited
  instruments, runner, report, and review record. Any later change to an
  audited instrument or primary protocol invalidates the pass until a complete
  new audit is reviewed. Post-result catalog weakening requires the documented
  deviation/new-study process.
- AUD-14: The public benchmark release MUST retain the machine-readable
  inventory and catalog, pristine fixture hashes, mutation patches/operators,
  per-case stdout/stderr and detector outputs, independent expected-value
  derivations, complete JSON reports including failed predecessors, review
  records, and a command that regenerates the report without confidential
  faults or credentials. A release missing any of these artifacts MUST NOT
  claim an independent instrument audit.

## Required fixtures and limitations

Acceptance coverage includes arithmetic sign and denominator errors, swapped
treatment labels, duplicate/dropped sessions, altered exclusion reasons,
off-by-one stopping and credit boundaries, censored rows, exposure unit and
window errors, validator false accepts/rejects, provenance substitution,
leakage/contamination detector bypasses, and stale or hand-edited figure/table
inputs. Negative controls include a pristine replay and a semantics-preserving
serialization or ordering change.

Mutation coverage demonstrates sensitivity to the seeded defect classes; it
does not prove the absence of unknown defects. The audit report must state that
limitation and preserve every surviving non-critical blind spot. An independent
review is an external gate, not evidence that the instrument is infallible.
