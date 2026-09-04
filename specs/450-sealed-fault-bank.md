# SPEC 450 — Sealed hidden fault bank and out-of-worktree injector

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #348. This contract does not
convert the three public H6 faults into blinded evidence and does not authorize
pilot or scored collection. It depends on the issue #353 sealed agent view,
CON-5/CON-7/CON-8, and the frozen protocol/statistical contracts before use in
issue #349.

A production **bank** is evaluator-private content, not a tracked repository
directory. Public code defines schemas, a generic injector, synthetic canary
fixtures, and audit tooling. Fault definitions, assignment seeds, activation
details, repair references, and the sealed ledger remain outside the agent view
and git object namespace until the benchmark version is closed and revealed.

## Versioned bank and diversity

- FLT-1: A production bank MUST have a versioned machine-readable manifest with
  an immutable bank id and content hash, lifecycle state, clean-baseline hash,
  compatible protocol/injector versions, creation and seal timestamps, and one
  private record per instance. Each instance MUST declare an opaque id; family;
  target and injection operator; persistence/activation rule; candidate severity
  ladder; expected evidence and degradation metric; repair class; safety review;
  calibration state; and release disposition. Unresolved fields MUST block seal.
- FLT-2: A sealed scoring bank MUST include perception, decision, motion,
  schema/metadata, clocking, and runtime families; both persistent and
  intermittent activation; multiple pre-pilot severity candidates per applicable
  family; at least two atomic coupled-fault instances spanning distinct targets;
  and no-fault sham controls. Mechanical coverage validation MUST fail on a
  missing category rather than accepting a prose assertion.
- FLT-3: The bank MUST contain pre-declared novel-repair instances whose accepted
  repairs require a small behavior-preserving or behavior-correcting change not
  obtainable by copying, reverting, or diffing against a participant-visible
  healthy reference. The manifest MUST distinguish novel repair, restoration,
  and diagnosis-only cases. The sealed scoring mix MUST contain at least two
  novel-repair instances spanning distinct families and the frozen minimum count
  in every other required repair class; otherwise it MUST be refused.

## Sealed storage, selection, and participant view

- FLT-4: Production bank bytes, clean reference material, injector ledger,
  assignment seed, reveal key, and accepted-repair oracles MUST reside outside
  every agent-visible worktree, git repository/object namespace, HOME/cache/temp
  view, and allowed tool root. Only their commitments and public schema versions
  may be tracked. The issue #353 external confinement adapter MUST prove denial
  before the controller opens a participant session.
- FLT-5: A frozen controller MUST derive assignments deterministically from a
  hidden campaign seed, bank version, randomization block, and session id. Before
  collection it MUST publish an externally timestamped commitment to the bank,
  assignment algorithm, seed commitment, protocol, and planned cell counts,
  without revealing identities. Duplicate assignment, seed reuse across closed
  versions, an unsealed bank, or operator-selected scored cells MUST fail closed.
- FLT-6: Participant worktrees, prompts, transcripts, ordinary logs, tool help
  and results, environment, argv/process listings, inherited descriptors,
  sockets, caches, and error messages MUST contain no bank/fault id, family,
  severity, activation rule, controller path, healthy diff, accepted repair, or
  reveal material. Injected participant-visible bytes MAY embody the defect but
  MUST contain no diagnostic marker or stable trigger naming it. Preflight and
  postflight MUST scan all retained participant-visible artifacts for seeded
  synthetic canaries and private-bank fingerprints; a hit invalidates and blocks
  the campaign rather than becoming an agent failure.
- FLT-7: The public injector MUST be a generic, content-addressed engine that
  receives an opaque controller handle rather than fault metadata on argv or in
  the participant environment. It MUST materialize from the declared clean hash
  into an evaluator-owned staging tree, validate target containment and expected
  preimage hashes, apply exactly the selected sham/single/coupled transaction
  atomically, and emit the postimage hash to the sealed ledger. It MUST refuse
  traversal, symlink/hardlink escape, dirty preimages, partial coupled changes,
  unknown operators, or a destination inside the participant worktree.

## Sham cells, calibration, and admissibility

- FLT-8: No-fault controls MUST traverse the same assignment, materialization,
  controller, timing, retention, and evidence paths as fault cells using a
  semantics-preserving sham transaction. Before the first sanctioned outcome or
  runtime evidence, participant-visible file counts/modes/timestamps, prompts,
  process/tool surfaces, controller messages, and treatment manifests MUST not
  reveal sham versus fault status. The leakage probe MUST freeze its metadata
  feature allowlist, classifier, held-out split, and chance/uncertainty decision
  rule before execution. If the classifier performs better than that frozen
  chance criterion, the bank remains blocked.
- FLT-9: Before calibration, each candidate instance MUST freeze a paired clean
  baseline, task/seed set, degradation outcome and direction, minimum meaningful
  degradation, sample size/precision rule, safety endpoints, stopping rule, and
  exclusion/retry policy. Every scored instance MUST measurably degrade that
  baseline under the frozen rule while preserving enough valid evidence to make
  localization possible. The task or seed is the calibration unit; repeated
  events within it MUST NOT be counted as independent replicates. The retained
  report MUST show the paired effect, uncertainty interval, achieved sample
  size, exclusions, and threshold decision. Ineffective, saturated,
  non-diagnostic, or unstable instances MUST NOT enter the scored bank.
- FLT-10: Severity calibration MUST use a campaign purpose and identities marked
  `excluded_pilot`, retain every attempted severity and no-fault run, and apply a
  pre-declared selection rule. Calibration outcomes MAY select or reject a
  severity before bank seal but MUST never enter confirmatory estimates or be
  exposed to participants. After seal, a severity, activation rule, efficacy
  threshold, or instance substitution requires a new bank version and protocol
  deviation; silent tuning from scored outcomes is forbidden.

## Frozen safety and evaluator integrity

- FLT-11: The bank schema and injector MUST enforce a positive target/operator
  allowlist that excludes the scorer, verifier, reset, budget guard, limits,
  authorization policy, environment/scene truth, evidence sink, controller,
  injector, and device/bridge drivers. Preimage and postimage hashes for every
  excluded asset MUST be identical. A fault MUST NOT grant oracle information,
  bypass guard/authorization, suppress evidence, relabel an outcome, or create a
  protocol-prohibited collision, limit breach, wrong-object action, or other
  semantic hazard; failure of any static or dynamic safety oracle rejects it.
- FLT-12: Scored injection MUST run only after a clean expert baseline and the
  instance's safety/efficacy calibration pass are bound to the exact environment,
  primitive, guard, evaluator, and task hashes. At runtime the controller MUST
  verify the selected transaction and frozen-asset hashes before and after each
  session. An injection failure, partial activation, unexpected recovery,
  evaluator drift, or unreadable ledger is infrastructure-invalid and remains in
  the session-flow record; it MUST NOT be scored as agent behavior.

## Sealed record, reveal, replay, and rotation

- FLT-13: For every assignment the sealed append-only controller record MUST
  retain campaign/session/block ids, opaque assignment, hidden-seed derivation
  proof, bank/injector/clean/postimage hashes, atomic operator receipts,
  activation timeline, sham/fault status, calibration provenance, frozen-asset
  audit, safety results, lifecycle/exclusion status, and raw evidence links. The
  participant-facing record MUST expose only protocol-sanctioned fields and
  opaque session identity before reveal.
- FLT-14: After the campaign is closed and outcomes/analysis inputs are frozen,
  an authorized reveal command MUST verify the pre-collection commitments and
  generate a machine-readable mapping from every assignment to family, target,
  persistence, severity, activation, and repair class. A replay command MUST
  reconstruct every selected postimage from the clean baseline, bank, injector,
  and revealed seed; compare hashes and activation receipts; and rerun the frozen
  safety/efficacy checks. Missing assignments, commitment mismatch, or replay
  drift MUST block publication.
- FLT-15: Acceptance tests MUST use synthetic canary banks, never production
  identities, to cover all required families/modes, deterministic selection,
  sham parity, atomic coupled injection and rollback, target escapes, frozen
  scorer/guard integrity, participant-surface canary leakage, sealed-ledger
  completeness, commitment/reveal verification, exact replay, and fail-closed
  corruption. Tests MUST prove the bank and ledger paths are absent from the
  agent-visible allowlist and inaccessible through every issue #353 route.
- FLT-16: A bank version MUST move monotonically through draft, calibration,
  sealed, scoring, closed, revealed, and retired states. Reveal is permitted only
  after campaign closure; the complete bank and audit artifacts MAY then enter
  that version's release. No revealed or previously participant-exposed instance,
  patch fingerprint, activation secret, or repair oracle may be reused in a
  future hidden bank. Future versions MUST publish a new commitment and document
  rotation without claiming secrecy from renamed identifiers alone.

## Historical boundary

The public H6 fault hooks and three-cell records remain useful unblinded
existence evidence. Their tracked fault menu, environment trigger, source,
per-cell healthy references, and agent access to the repository mean they MUST
NOT be relabeled as sealed-bank results, calibration for a future hidden bank,
or no-fault-controlled evidence.
