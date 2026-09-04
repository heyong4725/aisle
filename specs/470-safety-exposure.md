# SPEC 470 — Safety exposure ledger and held-command ablation

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #351. This contract separates
declared topology, kinematic enforcement, and semantic outcomes. It depends on
the issue #345 zero-event statistics and issue #350 `actuation-gateway` boundary
for confirmatory use. Simulation and fake-driver evidence do not establish
physical safety.

The budget guard reasons about commands, limits, clocks, and robot state. It
does not know medicine identity. The verifier observes semantic outcomes after
they occur. Neither a zero count nor verifier detection is prevention.

## Claim layers and raw exposure schema

- SFE-1: Every safety result and claim MUST identify exactly one evidence layer:
  `declared_topology`, `gateway_kinematic_enforcement`,
  `observed_kinematic_outcome`, `verifier_semantic_detection`, or
  `semantic_authorization`. A table or sentence MUST NOT use evidence from one
  layer to assert another. In particular, VAL-5 proves declared paths, guard
  decisions prove enforcement, and wrong-object rows prove observed detections.
- SFE-2: Every run MUST retain a versioned append-only exposure ledger joining
  stable campaign, session, environment, episode, task, seed, manipulation
  attempt, command proposal, gateway decision, driver receipt, verifier event,
  and exclusion ids. Rows MUST carry contract and monotonic timestamps, producer
  and instrument hashes, controller class, evidence kind
  (`unit|synthetic|simulation|hardware`), and source-record hashes. Duplicate,
  orphaned, cross-environment, or time-regressing ids MUST fail reconciliation.
- SFE-3: Exposure units MUST be derived by frozen rules. An episode begins at a
  retained reset/goal assignment even if no result arrives. For grippers, a
  manipulation attempt opens on the first close proposal after an open state and
  at least one arm-motion proposal since the prior boundary, and closes on the
  next open, reset, terminal result, or timeout; incomplete attempts remain
  counted and flagged. Other end effectors require a pre-frozen adapter. A
  delivery is one trusted verifier-observed object entry into the task delivery
  region, deduplicated by object/episode/entry id. A wrong-object event is such a
  delivery whose observed identity differs from the assigned target; this is a
  detected outcome, not a prevented command.
- SFE-4: Each received actuation request MUST have a proposal id and exactly one
  `pass`, `clamp`, `refuse`, or `hold` gateway decision plus zero or one driver
  receipt. The ledger MUST retain raw request/output hashes, channel, parsed
  values, decision reasons, per-axis/joint correction magnitude, policy/limit
  hashes, stamps, and receipt. It MUST report received requests, valid proposals,
  malformed refusals, interventions by decision, and distinct proposals with at
  least one intervention; violation records MUST NOT inflate a multi-axis
  proposal into multiple proposals.
- SFE-5: Collision rows MUST come from a frozen trusted contact instrument with
  threshold, bodies, magnitude/duration, and event id. Workspace exposure MUST
  separately record proposed out-of-envelope commands, gateway workspace
  interventions, driver-received out-of-envelope commands, and observed
  out-of-envelope state/duration. Missing collision/contact or observed-state
  instrumentation is `unmeasured`, never zero.

## Analysis and zero-event bounds

- SFE-6: A CON-8 analyzer MUST regenerate one machine-readable report and every
  derived table from named raw ledgers. By arm, controller class, evidence kind,
  and session it MUST report randomized/started/included/excluded episodes;
  manipulation attempts; deliveries; collisions; received/valid/malformed
  proposals; clamps/refusals/holds; workspace proposal/intervention/receipt/
  outcome events; wrong-object events; and all denominators and exclusion
  reasons. Reconciliation failure MUST return `ok: false`.
- SFE-7: A frozen source map MUST classify every command producer as
  `classical`, `learned`, `hybrid`, or `unknown` by content hash and protocol
  role, never by filename or self-declaration. Proposal rate MUST be distinct
  valid proposals per active contract-clock second; intervention rate MUST be
  distinct intervened proposals per valid proposal. Counts, active duration,
  rate, and uncertainty MUST be reported separately for classical and learned
  motion; uncertainty across campaigns MUST use session/task units rather than
  treating proposals as independent. Hybrid/unknown remain visible and MUST NOT
  be silently pooled.
- SFE-8: Every zero-event result MUST use the issue #345 exact one-sided binomial
  upper bound with confidence level, event count, and explicit independent unit
  and denominator. The primary bound MUST use exposed sessions/tasks with at
  least one event as the binary outcome unless a human-ratified protocol names
  and justifies another independent unit. Wrong-object outcomes MUST also report
  delivery and manipulation-attempt denominators when both exist, and collisions
  MUST name their declared opportunity denominator, but nested opportunities
  MUST remain descriptive rather than inflate the binomial sample. Mixed,
  missing, zero, or event-incompatible denominators MUST fail closed instead of
  producing `0%` or a bound.

## Fixed-proposal guard ablation

- SFE-9: Before execution, a machine-readable ablation protocol MUST freeze
  paired command-trace ids and hashes, initial states, task/seeds, timing source,
  gateway/guard/driver/environment hashes, arm order/randomization, primary and
  secondary endpoints, smallest effect of interest, sample size/precision rule,
  stopping rule, exclusions/retries, analysis seed, and exact commands. Each
  pair MUST replay byte-identical proposals and contract timestamps; only the
  gateway enforcement mode may differ.
- SFE-10: `guard_on` MUST apply the production policy. Evaluator-only
  `guard_observe_only` MUST compute and log the identical would-have decision
  while forwarding each well-formed proposal unchanged. Observe-only authority
  MUST NOT enter a participant process or run on physical hardware; it is
  permitted only with an isolated fake driver or simulator under a separate
  emergency containment envelope that is identical across arms and whose own
  interventions invalidate the pair.
- SFE-11: The frozen trace set MUST include legal negative controls; joint,
  velocity, workspace, gripper, base keep-out and arm/base mutual-exclusion
  proposals where applicable; and a held-motion trace with silence beyond the
  watchdog deadline. The primary endpoint MUST be per-trace driver-received
  kinematic violations. Secondary endpoints MUST include observed
  out-of-envelope duration/magnitude, collisions, intervention false positives,
  and lease/watchdog stop behavior. Legal traces changed by guard-on or an unsafe
  guard-on receipt MUST block the study.
- SFE-12: The trace or task-session, not an individual command or violation row,
  is the experimental unit for the causal contrast. Analysis MUST retain and
  report every pair, exclusions, paired effect with uncertainty, negative/null
  results, and strata by violation class. Event rows MAY describe mechanisms but
  MUST NOT be treated as independent replicates.

## Retention, wording, and hardware boundary

- SFE-13: Release evidence MUST retain raw proposal streams, initial states,
  gateway decisions, violation records, driver receipts, observed state/contact
  streams, verifier events/sidecars, reset/goals/results, source maps, ledger,
  protocol, analysis output, generated tables, environment/instrument hashes,
  and the exact regeneration command. Hand-entered totals or tables are not
  evidence; a changed raw input MUST change the report hash.
- SFE-14: Claim-bearing H5, project, and paper wording MUST state separately that
  validated declared paths traverse the guard, measured gateway interventions
  alter kinematically illegal proposals under the tested boundary, and the
  verifier counts observed semantic outcomes. It MUST NOT say wrong-object safety
  is `by construction`, that the guard knows identity, that verifier detection
  prevents an event, or that zero observations prove impossibility. A
  machine-readable occurrence audit MUST classify and reject broader claims.
- SFE-15: Hardware-independent work MUST provide the schema, analyzer,
  reconciliation fixtures, fake-driver and simulation ablation, source mapping,
  zero-bound tables, dry-run commands, and hardware adapter/contact-event
  contract. Hardware ledgers MUST remain absent or `hardware_pending` until
  generated by a calibrated physical instrument; simulated contacts, gateway
  receipts, and held-command timing MUST NOT be relabeled as physical exposure.

## Required fixtures and limitations

Fixtures include missing episode results, incomplete manipulation attempts,
duplicate deliveries, multi-joint clamps sharing one proposal, malformed
refusals, absent contact instrumentation, mixed evidence kinds, zero and nonzero
events, source-class drift, missing driver receipts, legal/illegal paired traces,
watchdog silence, emergency-containment activation, and raw-input hash changes.
The ledger quantifies the recorded exposure surface; it cannot prove absence of
unmeasured hazards or semantic prevention.
