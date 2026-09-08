# Matched-session implementation acceptance audit

Issue #519 is open. This is a current implementation audit, not authorization for
pilot or confirmatory collection, expert parity, or independent gate sign-off.
The supported engineering entry point and input formats are documented in
[matched-session.md](matched-session.md). Requirements come from MON-8, MON-12,
MON-13 and CSE-1; engineering records remain isolated under `expert_parity`.

## Issue acceptance

| Requirement | Implemented evidence | Remaining acceptance work |
| --- | --- | --- |
| Bind the arm, treatment/interface, editable surface, launch/controller/broker, documentation/prompt, evidence schema and shared model/tool/authority/budget/environment identities. | `matched_session` admits both arms, hashes controller sources and runtime trees, verifies declared ambient state, and binds development parameters. `test_matched_session.py` covers immutable identity and source drift. | Final review of the full source/runtime dependency closure and authority surfaces. A hash receipt alone does not prove suitable runtime authority. |
| Refuse missing, stale, asymmetric or undeclared identities before participant execution; retain invalid attempts and reject mixed-identity resume. | Admission, active-plan checks, fresh HOME/snapshot/allocation reservations, exact run configurations, and pre/postflight checks reject drift. Session/tool tests retain cancellation, timeout, malformed telemetry and refusal records. | Complete descendant/resource enforcement and concurrent-replacement review; direct-child cleanup and path preflight do not establish those properties. |
| Emit one versioned common envelope for both arms with lifecycle, errors, snapshots, budgets, transcript/tool events, run/evaluator evidence, audits and hashes. | `matched_evidence` combines verified controller observations, raw run collections, evaluator/guard records, frontend observations and run verdicts. Monolithic absence of typed validation is a declared treatment property. | Global frontend-tool coverage, frontend/controller linkage, total simulator work and final session-completeness reconciliation remain unresolved. `complete` remains false; unobserved quantities remain null. |
| Exercise real local process boundaries for both paths, including failure and drift. | Real run children, confined typed/monolithic workers, actual validator capability probes, and end-to-end dynamic preparation/refusal journals are tested. | Ordinary typed and monolithic rollouts have completed engineering episodes with successful worker exits. Both admitted engineering session paths now retain verified service and run journals; common-session completeness and final review remain unresolved. These results are not expert or coding-agent baselines. |
| Preserve study prerequisites without inventing attestations. | Scored purposes are refused. Engineering records remain ineligible for treatment-effect estimation. | Expert authorship/parity, frozen protocol, independent statistical review, private evaluator separation, external confinement and operator evidence remain separate gates. |
| Preserve legacy behavior and document the supported entry point. | Legacy campaign identity/purpose behavior and the optional monolithic worker path have regression coverage. The dedicated matched CLI and controller APIs are documented. | Full repository regression run, final review/simplification, and GitHub delivery gates must pass before shipping. |

## Execution and authority boundaries

The participant frontend, trusted run controller, validator, authored worker and
simulator/evaluator are distinct roles. A participant's filesystem policy does
not grant the trusted controller's write authority. Conversely, authored Python
must not inherit the controller's access to records, the other arm, trusted
implementations, or evaluator-private assets.

The controller reserves a run before checking its private preparation template.
Allocation must be fresh and disjoint from participant/controller/runtime state.
The typed path captures the complete editable graph, turn plan, four manifests
and four Python sources alongside pinned validation dependencies. Normal
validation treats authored code as data. The monolithic path captures the
current module without importing it. Both paths seal their source and runtime
bindings into a private run configuration before starting the real child.

The typed child provisions stages on demand. Node selection follows authored
source paths and graph-defined IDs. Each launch receives fresh worker bundles,
HOMEs, profiles and capability evidence. Graph transformation preserves validated
input wiring, including routes from renamed nodes into trusted consumers, while
pinning trusted executable/runtime settings. It neither repairs the candidate
nor recompiles the authored turn plan. Renamed-node execution is covered;
multiple-instance selection is covered only by a selection test so far.

The monolithic child provisions one bound worker configuration. A receipt binds
its path/hash and module hash to the sealed run. The authored constructor and
callbacks execute in the worker; the trusted broker owns primitive objects,
validates actions and maintains the handle table. Typed hosts similarly own the
Dora node, output grants, turn accounting and `turn_done`; authored nodes use a
remote facade. Neither worker receives a simulator or evaluator handle.

Admitted validator execution uses the canonical interpreter and explicit verified
runtime import roots. Worker bootstraps also receive bound import roots after
their curated bundles. Added roots are not processed with `addsitedir`, and their
`.pth` files are not executed. Actual tests supply copied dependencies rather than
assuming that an ambient virtual environment is readable. Standalone low-level
command helpers and synthetic adapter fixtures are not evidence of an admitted
or confined execution path.

## What the actual process tests establish

- `test_dynamic_typed_journal.py` uses actual validator capability probes and
  sandbox execution, then the real matched run child and four dynamically
  provisioned workers. The fixture intentionally reaches the ordinary
  environment-hash gate refusal because its controller copy is not a Git repo.
  It retains preparation and the refusal; it does not bypass the gate or launch
  a simulation. Outer participant admission remains an explicit fixture.
- `test_dynamic_monolithic_journal.py` reaches a real confined authored failure
  through ordinary preparation, the run child, worker execution and the parent
  journal. It verifies preserved worker evidence, not a successful episode.
- `test_worker_declaration.py` covers actual worker execution. The typed case
  receives an Arrow turn, returns nullable uint64 data, verifies denied controller
  source reads, and leaves turn completion to the host. The monolithic case
  initializes a controller, processes an event and closes its RPC session.
- `test_typed_provider_execution.py` runs all four baseline workers, and a
  normally validated renamed-node variant, through generated host configurations
  under the actual sandbox. Both stage audits pass. Host turn events and the
  validator adapter in this particular test are fixtures, not Dora transport.
- `tests/graph/test_typed_worker_dora.py` verifies the pinned Dora installation,
  launches an actual sender and trusted host, provisions an actually confined
  authored worker, delivers a stamped float32 observation with Dora datetime
  metadata, and receives nullable uint64 results plus the host-owned turn
  acknowledgement. Values, turn stamps, emitted counts and shutdown survive the
  real transport. This is a small engineering graph, not the ordinary rollout
  graph, simulator execution, or outer matched-session admission.
- `tests/graph/test_monolithic_worker_dora.py` provisions an actual confined
  worker, prepares a hash-bound configuration, and runs the production broker
  through Dora. A goal and joint-state observation produce validated gripper and
  feedback messages with preserved goal/turn metadata and host-owned completion.
  The worker closes successfully. The fixture has no simulator, actuator, scorer
  or outer matched-session admission.
- The local `519-typed-worker-engineering-2` probe used actual validator capability
  checks, the normal validator, dynamic worker provisioning, the ordinary rollout
  gates, pinned Dora and Genesis. The oracle recorded one successful T1 episode
  for public development seed 7. All four workers completed input, exited zero and
  passed typed postflight; the run manifest retained traces and indexed worker
  artifacts with no stage error. Its predecessor was refused because the probe
  supplied the participant path instead of the sealed snapshot path; that refusal
  remains retained. This used `env_baseline=local` and the unscored `expert_parity`
  purpose. It did not run a participant frontend or establish matched-session
  completeness, independent parity, or a success-rate estimate. The retained
  `test_typed_worker_rollout.py` checks real episode and worker evidence without
  requiring per-seed physics outcomes to be identical. Its separate execution
  passed in 425.05 seconds; retained run
  `typed-worker-integration-80187a6779764484934baf0684becb61` recorded its own
  successful oracle episode (seed 7, simulation end 15.16 seconds), no stage
  error, and successful postflight for all four workers. Neither episode
  duration nor test wall time establishes total simulator-work accounting.
- The local `519-monolithic-worker-engineering-1` probe used the unchanged
  monolithic expert, actual worker capability provisioning, source-bound module
  checking, normal graph stamping, and an ordinary Dora/Genesis rollout with
  `env_baseline=local`. The oracle recorded one successful seed-7 episode ending
  at 15.16 simulation seconds. The worker closed with exit zero and no error,
  after 1,998 commands and 5,008 primitive calls (14,012 RPC messages). The
  rollout returned `ok=true`, `stalled=false`; its reported wall duration was
  282.7 seconds. This invoked `check_module`, `stamp_graph`, and `rollout`
  directly: `monolith.run` does not expose the local engineering override.
  The separate `test_monolithic_worker_rollout.py` execution passed in
  259.44 seconds, verifying its own retained episode and clean check/run worker
  receipts. No campaign ledger was charged, and no participant frontend, full session
  envelope, independent parity gate, or effect estimate was established.
- The first actual-repository matched monolithic journal probe passed the normal
  idea and protected environment gates, but its declared controller `PATH` omitted
  `/usr/sbin`. Genesis could not execute macOS `sysctl`, and no episode was
  recorded. The controller retained the run/episode reservation, raw process and
  worker evidence, and an infrastructure exclusion; `audit_tool_journal` verified
  the retained failure. This proves a real startup-failure journal, with outer
  participant admission still an explicit fixture. The failed attempt remains
  separate from a fresh attempt with the corrected declared path.
- The corrected-path journal attempt reached a successful oracle episode, but
  exposed a nested deadline defect: the outer controller reused the 300-second
  rollout timeout and killed the child before its manifest/worker collection
  finished, despite a 900-second admitted tool budget. The journal audit rejected
  the incomplete worker collection. Nine verified orphan processes required
  separately recorded manual cleanup. A regression now checks both arms: the
  enclosing transaction uses remaining tool time while preserving the inner
  rollout timeout. The next actual attempt (`matched-2176dff9084895e9c62ca2d7`)
  returned a successful child and tool result after 481.025 seconds; the journal
  audit verified 14,036 retained worker files and the oracle episode. Its worker
  receipt records a clean exit, 1,998 commands and 5,008 primitive calls. This
  verifies the prepared-run deadline correction through the ordinary protected
  environment and idea gates, with outer participant admission still a fixture.
  Both prepared and direct paths now use the remaining admitted tool time for
  child supervision while preserving the inner rollout execution timeout.
  Configured and direct launch paths now share observed-descendant cleanup on
  timeout/cancellation. The regression observes detached children, verifies that
  unrelated processes survive, and refuses signalling a reaped controller PID.
  The focused cleanup regression passed all seven cases; the broader launch,
  tool, journal and freeze regression passed 96 tests in 185.17 seconds. An actual
  Dora forced-termination probe now passes for an owned controller, Dora CLI,
  timer-driven node and detached child: three observed descendants stopped and
  an unrelated process survived. Two retained predecessor probes exposed a
  conservative false failure when macOS changed an exiting Dora executable name
  to `(dora)`. Cleanup now waits for that identity to disappear without signalling
  it. Evidence is retained in `/private/tmp/aisle519-dora-cleanup-3`; this small
  real-Dora graph does not establish exhaustive cleanup. A subsequent actual
  monolithic simulation probe (`/private/tmp/aisle519-monolithic-cleanup-1`)
  injected the controller timeout after Dora/ffmpeg process-tree readiness.
  Its cleanup receipt observed 19 descendants and reported no remaining
  processes or errors; a fresh identity check also found no survivors from the
  pre-timeout snapshot. The run journal rejected incomplete worker collection,
  as required for the interrupted run. The probe used an unsupported idea-close
  verdict after execution; that bookkeeping record was finalized separately
  without rerunning the simulation. This covers the observed simulation-tree
  timeout path, not already-reparented descendants or exhaustive containment.
- The actual-repository typed journal probe (`matched-7f3db3841c38b2b57f09bc88`)
  passed normal validator and worker capability checks, then the protected
  environment, idea and rollout gates. Its oracle episode succeeded; all four
  workers passed postflight with exit zero and no stage error. The child and tool
  results succeeded, and the journal audit verified one tool, run and episode
  reservation after 640.600 seconds of tool time. Its first setup attempt had
  correctly refused a not-yet-created hidden directory. These independently
  configured engineering probes do not form a matched pair or a complete frontend
  session; no study gate or effect estimate follows from them.
- Session/service tests cover provisioned preparation through the session API
  with deterministic frontend processes. They do not establish external coding-
  agent event completeness or participant parity.

The capability producer checks 18 required cases against the supplied Python-only
profile, with unrestricted controls for denied operations. It retains filesystem,
Git-object, executable and loopback observations, checks identities and write
effects, and cleans up only its owned fixtures. These tests do not exhaust every
IPC/process route or establish independent study confinement.

## Actual frontend integration evidence

The monolithic frontend probe retained a successful ordinary simulation and
verified ToolService journal. The first typed frontend probe failed at turn 1:0:
Dora attachment occurred before repeated worker launch verification, so setup
consumed the first turn's watchdog. All failed records remain retained. The host
now defers transport attachment until a started worker requests it; a deterministic
real-process regression failed before that correction and passed afterward.

A fresh typed frontend rerun then completed seed 7 successfully at simulation
time 15.16, with no retries. All four workers exited 0, and the tool audit verified
one request, run and episode. Evidence is indexed by
`/private/tmp/aisle519-typed-frontend-2/engineering-summary.json`; the original
failed attempt is retained under `aisle519-typed-frontend-1`. Both successful
frontend probes used actual sandbox profiles and explicitly incomplete full
access logs. Their final sessions remain infrastructure exclusions and their
common evidence remains incomplete. Synthetic zero token usage measures no
coding-agent performance.

The independent probe plans did not establish a matched pair. Their treatment
surfaces matched at the initial cross-plan audit, but additional private evidence
paths differed in permission identity. Admission now supports explicit verified
`private_roots` roles, retained through the CLI and rechecked on plan verification.
Only those exact paths normalize; undeclared hidden paths still differ. Fresh
paired-session provisioning must use the new bindings and verify all shared
identities before execution. This does not retroactively change retained plans.

New actual-capability plans in
`/private/tmp/aisle519-fresh-frontend-plans-2/report.json` verify equal normalized
treatment identities and controller surfaces using those roles. Exact runtime,
run-controller and validator bindings still differ across their fresh directories;
the report retains `all_shared_identities_verified: false`. No frontend execution
or paired-session equivalence follows from this provisioning check.

A separate contamination probe found that fresh admission accepted prior files
in the run-controller HOME, validator HOME and validator snapshot storage. Fresh
and inactive-arm admission now requires the generated empty HOME layout and empty
snapshot storage; the owning active arm may retain its current state. Twelve
regressions failed before this fix, and 215 admission/preparation/validator/freeze
tests passed afterward. The new actual plans also reject all three owned sentinel
files, with each removed afterward and the clean plan reverified. Results are in
`/tmp/aisle519-private-state-audit-fixed.json`. This closes that admission gap,
not the remaining lifecycle or shared-binding verification work.

## Fresh shared-runtime session checks

The `aisle519-shared-runtime-plans-5` probe built two independent fresh plan,
view and HOME sets against the corrected controller sources. Both passed actual
capability checks and admission. They share the immutable runtime and validator
bundle. Before execution, `declared-condition-audit.json` reverified both plans
and found equal controller and validator execution declarations after applying
its explicit fixture path-role mapping. Raw capability observations remain
separate; this comparison does not establish exhaustive authority or study
admission.

The typed session completed run `matched-d9d3b6aa936ad1455d6b97ec`; the monolithic
session then completed `matched-aac8fa7526941112c1943c0c`. Each retained one
successful public seed-7 oracle episode, one controller request, one reserved
run and episode, and a verified service/tool journal. Both deterministic frontend
processes exited zero with complete retained streams. Controller tool time was
680.015 seconds for typed and 563.532 seconds for monolithic. These are individual
engineering observations, not a cost comparison or coding-agent baseline.

Both final sessions were infrastructure exclusions solely because the supplied
full access logs explicitly declared incomplete coverage. Treatment identity,
visible-tree and confinement-active postflight checks passed without drift.
`common_evidence.complete` and eligibility remain false. Full frontend coverage,
request linkage, total simulator work and independent study gates remain
unresolved. The result and raw evidence are retained separately under each arm's
`session-result.json` and `tool-evidence` directory in
`/private/tmp/aisle519-shared-runtime-plans-5`.

The combined implementation passed all 3,106 unit tests, formatting and lint
checks after the request-parser, event-framing and configuration-size corrections.
Advancing its base to merged PR #533 preserved all 1,472 source-file entries
byte-for-byte. Final implementation review and delivery gates still apply.

## Evidence integrity and incomplete accounting

Controller journals reconcile reservations, attempt identities, observed process
results and retained stdout. A run summary preserves its verdict, returned result,
process status, preparation diagnostics and result artifact path even when no
simulation manifest exists. Collection integrity never changes the run outcome.
Missing, extra, redirected or altered indexed files invalidate their audit.
Service verification additionally requires integer seen/processed counts matching
indexed attempts, no reported service error, and an exact retained request-file
inventory. Four regressions exposed false acceptance of those inconsistencies;
the corrected service/journal/freeze suite passed 63 tests. These checks establish
controller request coverage, not linkage to every frontend tool invocation.

Dynamic monolithic evidence binds the captured module and generated worker to the
sealed provider request. Dynamic typed evidence checks its closed provider
inventory, preparation snapshot, stage identity/location and indexed stage files.
Raw capability retention is not a semantic audit of every observation. Typed
stage audits and monolithic worker collections retain underlying RPC bytes and
terminal records; deeper protocol/completeness review remains required.

Frontend observation counts currently describe recognized events, not certified
coverage of every tool. Controller tool requests and frontend calls are separate
measures until their linkage is verified. Episode duration omits reset work and
cannot stand in for total simulator work. A complete session cannot be inferred
from symmetric dictionary keys, file presence, a zero exit code, or component
passing counts. Nested runs and episodes remain attached to their parent session
and never become independent treatment replicates.

## Remaining software work before completion

1. Reconcile supported frontend events with controller requests and enforce the
   declared total tool budget without double-counting or omitting calls.
2. Record total simulator work, including resets/relaunches and partial failures,
   and use verified accounting in final session completeness.
3. Connect both ordinary simulation paths to the full matched-session journal.
   Both ordinary rollouts and controller tool journals have successful engineering
   probes; final common-session
   completeness is still unresolved. A deterministic lifecycle probe at
   `/private/tmp/aisle519-pair-lifecycle-audit-1/report.json` confirms that a
   normal private-HOME write by the typed arm leaves its active checks valid
   but blocks fresh-plan and monolithic-arm admission under that original plan.
   This proves that mutable plan reuse is unsupported, not that CSE requires
   such reuse: CSE-1 and CSE-12 require fresh independently built sessions and
   exclusion of prior-arm state. The integration must provision fresh per-session
   plans and verify shared treatment/block identities, or provide an explicit
   verified lifecycle transition. Ignoring other-arm drift is not an acceptable
   workaround. A deterministic regression now builds two independent plan/view/
   HOME sets, verifies equal normalized treatment identities and controller
   surfaces, and confirms that first-session state invalidates only its reused
   plan while the second remains admissible. This supports fresh-session
   provisioning; it is not a complete paired-session runner or an OS access-log
   attestation. Keep gate and authored failures covered.
   A subsequent actual macOS admission probe retained at
   `/private/tmp/aisle519-shared-runtime-plans-3/report.json` built and verified
   two fresh plans using physically shared immutable runtime paths and the same
   validator bundle. Runtime records now compare exactly, as do launch bindings
   and development parameters. Controller and validator records still differ;
   `component-differences.json` retains the differing field paths, including
   private state, policy roots and derived attestation hashes. Their equivalence
   is unverified. Both normalized treatment and controller-surface comparisons
   passed, but `all_shared_identities_verified` remains false. This probe did not
   execute a frontend or simulator and does not establish a complete session.
4. Finish runtime/HOME ownership and source-closure review, plus complete
   descendant lifecycle/resource enforcement. Killing a controller process group
   does not prove cleanup of workers or probes that started separate sessions.
   Pipe deadlines do not preempt trusted primitive calls or bound native Arrow
   parser allocation. Review handle reclamation and retained RPC semantics too.
5. Extend codec coverage review beyond the exercised T1 oracle rollout. An audit
   of its four retained worker RPC journals verified frame hashes and exact
   payload/metadata re-encoding across 19,944 frames. Observed payload types were
   null, uint64, string, uint32, float32 and int32, including depth/segmentation,
   joint state, commands and task messages. Evidence is retained in
   `/tmp/aisle519-typed-codec-audit.json`. This verifies that rollout's exercised
   payloads, not every tier or permitted candidate output. Typed transport also
   supports other primitive numeric, boolean and large-string arrays; list,
   struct, dictionary, binary and timestamp arrays remain unsupported.
   `test_typed_payload_coverage.py` additionally exercises all twelve declared
   numeric/boolean types through both payload and wire codecs, using integer
   and finite floating limits, nonzero-offset slices, empty arrays and all-null
   arrays. All 36 cases passed. This extends primitive codec coverage without
   claiming support for structured types or native-allocation bounds.
6. Complete the requirement-by-requirement review, simplification, repository
   formatting/lint/unit gates, affected simulation/graph and acceptance checks,
   then commit, push, PR review, green CI and the authorized merge workflow.

These are open work items, not reasons to replace unknown evidence with success.
The integration baseline passed 2,931 unit tests before the frontend telemetry
classification fix; that fix passed 176 frontend/session tests and 23 registry
tests. The real Dora worker graph passed separately. These results do not replace
the final ordered pre-commit gates or prove all acceptance requirements. The
worker RPC layers were merged separately in PRs #522 and #523; #519 remains open.

## Freeze and external prerequisites

BND v9 and CSE v5 are unpublished successor drafts. Published BND v8 and CSE v4
are not rebuilt. Draft refreshes preserve seed commitments and pending gates;
CSE v5 retains the unresolved #519 acceptance gate. Shared CLI/source changes
explain the successor records but do not constitute independent review or freeze.

No implementation test authorizes pilot/confirmatory collection, supplies expert
authorship, substitutes for a separate parity operator, grants private evaluator
access, or supplies physical evidence. Dedicated campaign-agent access and the
independent review/operator/evaluator gates remain explicit external prerequisites.

## Simulator operation accounting and shutdown verification

Matched runs now bind a per-launch work journal into the trusted simulator's
execution-graph environment. The controller writes an exclusive graph-hash
receipt before each spawn. Missing or interrupted journals remain incomplete;
completed step counts remain lower bounds when step completion is unresolved.
The collector recomputes work from retained journals and executed graph hashes,
and the tool audit rejects forged derived summaries. Initial spawn and teardown
failures still attempt cleanup and retain accounting evidence.

The real monolithic accounting test passed for run
`monolithic-worker-integration-a739f02a4ca14409a95b004f7c0fe3c0`. Its paired typed
attempt, `typed-worker-integration-88bc8cdf31ed47db8352b070910f724d`, completed an
oracle episode but failed worker postflight: hosts kept waiting on cyclic inputs
after their coordinator input closed, then manual shutdown interrupted host
verification. That failed attempt and its journals remain retained.

The trusted typed transport now delivers coordinator-input closure and ends its
iterator. The unchanged postflight assertions passed on rerun
`typed-worker-integration-6a77ff3024f24962bea7d6b89236e39c`: all four hosts verified
and exited normally, stage postflight passed, and retained accounting recomputed
successfully. The rerun took 638.45 seconds with no skips. The focused host/request
suite passed 37 tests; evidence collection/audit and cleanup regression results
are separate checks, not a substitute for the outstanding full repository gates.

Both accounting integration paths recorded one build, two resets and 1,521
completed physics steps (15,210,000,000 environment-simulation nanoseconds).
These are explicit wrapped-operation counts, not total simulator resource use.
Render and other unwrapped work, complete producer coverage, global frontend-tool
coverage and session-level reconciliation remain unresolved. The common envelope
therefore still leaves total simulator work unclaimed. Test duration is not an
estimate of accounting overhead, and these development runs establish neither
expert parity nor study eligibility.

The common envelope also exposes `simulator_operations`: completed environment-steps
and simulation nanoseconds summed over re-audited run journals, with run and attempt
provenance. Missing runs and interrupted journals prevent an exact recorded-step
claim; no observations produce null quantities. This partial aggregate does not
change the unclaimed total simulator-work field or session completeness.
