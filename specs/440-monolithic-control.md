# SPEC 440 — Equal-capability monolithic control surface

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #344. This contract defines
infrastructure and expert-parity evidence, not agent-treatment results. It does
not authorize pilot or confirmatory collection. It depends on CON-5/CON-7/CON-8,
HAR-2/HAR-4, the issue #353 treatment-integrity boundary, and the issue #345
session-level protocol before use in issue #347.

The comparison isolates a bundled engineering interface. The typed arm edits
capability manifests and dora dataflow YAML and receives AISLE static
validation. The monolithic arm edits one ordinary orchestration module and
receives only language/import/runtime failures. Robot functionality, trusted
evaluation, agent authority, budgets, and evidence are held fixed.

## Frozen treatment surfaces

- MON-1: A versioned machine-readable treatment-difference table and generated
  human-readable rendering MUST enumerate every agent-visible and runtime
  surface in both arms. Each row MUST classify the surface as identical,
  representation-equivalent, or intentionally different; name both artifact
  paths and hashes; justify any difference; and identify its planned analysis
  treatment. An undeclared difference or unresolved parity field MUST block
  expert parity and all agent sessions.
- MON-2: The typed deliverable MUST consist only of the frozen set of
  agent-editable node implementations, capability manifests, and dora YAML
  declared by the protocol. It MUST use the pinned capability resolver, static
  schema/topology/motion-gating validator, and dora runtime, with their normal
  structured diagnostics. The harness MUST NOT repair or complete an authored
  graph on the agent's behalf.
- MON-3: The monolithic deliverable MUST consist of one declared orchestration
  module or script using a frozen, documented primitive API. That view MUST NOT
  expose capability manifests, registry search/resolution, dora graph YAML,
  AISLE validator entry points or diagnostic catalog, or generated equivalents
  of those facilities. The launcher MAY surface ordinary language syntax,
  import, exception, timeout, and process-exit errors, but MUST NOT add a hidden
  static topology/type checker or automatically repair the module.
- MON-4: Both arms MUST expose the same pinned robot-primitive implementations,
  semantic observation fields and cadence, action names/arguments and
  authority, task goals, development seeds, reset semantics, limits, episode
  budget, and held-out evaluator. A machine-readable interface map MUST relate
  transport-specific envelopes field by field and fail on a missing, extra, or
  differently privileged semantic field. Transport/runtime metadata needed
  only by one arm MUST be declared in MON-1 and excluded from task authority.

## Trusted boundary and non-bypassability

- MON-5: Environment ownership, reset, limits, budget guard, verifier, held-out
  scorer, seed selection, and evidence capture MUST remain in a frozen trusted
  controller outside both agent-editable deliverables. The controller MUST hold
  the only simulator/device and evaluator handles. The monolithic module MUST
  call a narrow primitive broker whose motion calls traverse the same pinned
  guard implementation and limits as typed-arm motion.
- MON-6: Neither arm may write the trusted controller, primitive broker,
  implementations, guard, reset, limits, verifier, scorer, hidden seeds, or raw
  evidence sink. The issue #353 external confinement policy MUST deny alternate
  filesystem, process, network, socket, import, and git-object paths to those
  assets. The controller MUST fail closed before actuation or scoring when its
  frozen hashes, confinement status, route map, or evidence sink are unresolved.
- MON-7: Conformance tests MUST attempt direct driver/bridge calls, unguarded
  motion, reset invocation outside the broker, scorer/verifier imports, hidden
  seed reads, monkeypatching, subprocess/socket/network access, path traversal,
  and replacement of trusted modules. Every attempt MUST be denied or produce
  an infrastructure-invalid record before a score; merely asking the agent not
  to bypass the boundary is insufficient.

## Matched treatment and expert parity

- MON-8: Session preflight MUST extend the issue #353 treatment tuple with arm,
  treatment-table id, interface-map id, editable allowlist, launcher/broker
  hashes, documentation/prompt hashes, and common-evidence schema. Model,
  system prompt apart from representation-specific instructions, tools,
  approvals, filesystem/process/network authority, development and episode
  budgets, wall/token accounting, environment, randomization, host load, and
  evaluator identities MUST otherwise match. Necessary instruction differences
  MUST appear in MON-1 rather than being hidden in prose.
- MON-9: One human-authored expert deliverable MUST exist for each arm with
  authorship/provenance and immutable hashes. Each expert MUST use only the
  files, documentation, observations, actions, and tools allowed to an agent in
  that arm. Both deliverables MUST be frozen before a separate operator reveals
  or executes the parity seed set; evaluator-private values and the other arm's
  solution MUST remain unavailable to each author.
- MON-10: Before execution, a machine-readable expert-parity protocol MUST
  freeze task instances, paired seed identities, minimum functional acceptance,
  score equivalence margin and rule, safety/limit rules, observation/action
  interface checks, resource ceilings, stopping rule, exclusions, and the
  exact parity command. Every assigned run MUST remain in the record; an
  infrastructure retry MUST follow the frozen retry rule, and any unresolved
  exclusion keeps the gate blocked. The gate passes only if both expert
  deliverables meet every functional and safety threshold on every paired valid
  run, every declared paired score difference satisfies the frozen equivalence
  rule, the interface map is exact, and all common evidence is complete. A
  failure, post-result margin, or manual override MUST keep the gate blocked.
- MON-11: Expert-parity and engineering-shakeout records MUST use a distinct
  `expert_parity` campaign purpose and immutable campaign id. They MUST never be
  pooled with pilot or confirmatory agent sessions, used to estimate a treatment
  effect, or counted toward issue #347's sample size. Failed and superseded
  parity attempts remain retained with their exclusion/invalidation reasons.

## Common evidence, identity, and freeze

- MON-12: Both arms MUST emit the same versioned base-evidence envelope required
  by the primary build experiment: assignment/session/treatment ids and lifecycle
  timestamps; agent transcript and tool events; token, wall, tool, and simulator
  budgets; authored and final deliverable snapshots; edit/check/run attempt and
  error events; run manifests; raw episode streams and seed ids; evaluator
  outcomes; guard interventions; exclusions; preflight/postflight audits; and
  content hashes. Arm-specific payloads MAY be nested, but absence of a typed
  validator in the monolithic arm MUST be represented as a declared treatment
  property rather than missing data.
- MON-13: Preflight and postflight MUST verify the treatment-table, interface,
  allowlist, expert/agent deliverable, launcher, broker, primitive, controller,
  evaluator, evidence-schema, and documentation hashes. A cross-arm artifact,
  forbidden typed facility in the monolithic view, undeclared helper, frozen
  drift, or mixed treatment identity MUST fail closed as infrastructure-invalid
  and remain in the session-flow record.
- MON-14: Unit and acceptance coverage MUST pin the single-file monolithic
  allowlist, absence of typed facilities, semantic interface equality, same
  primitive revisions and guard route, all MON-7 bypass attempts, treatment
  identity/resume refusal, evidence-envelope equality, expert freeze, parity
  decisions at and around the equivalence boundary, and exclusion of parity
  records from pilot/confirmatory analysis.
- MON-15: Protocol freeze MUST record immutable hashes for the treatment table,
  interface map, both surface definitions and documentation sets, trusted
  controller/broker/primitives/evaluators, confinement profile, common-evidence
  schema, expert artifacts, parity protocol, raw parity records, and parity
  report. Any post-freeze behavior change requires re-running parity and the
  documented deviation/new-study process; release artifacts MUST retain both
  arms and all parity evidence needed to regenerate the gate.

## Interpretation boundary

This two-arm design estimates the effect of the bundled typed-dataflow
engineering surface: manifest/graph composition, registry resolution, static
validation and diagnostics, and its runtime representation versus a single
ordinary module. It does not isolate static types from teaching hints or dora
runtime/transport overhead, prove that all real-world monoliths are equivalent,
remove agent stochasticity, or establish hardware validity. Those limitations
must appear beside any treatment effect. Expert parity establishes access and
functional capability, not equal agent difficulty or a causal result.
