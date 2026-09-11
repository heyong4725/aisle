# ADR-65: Frontend conformance is a bound evidence gate

Status: PROPOSED — engineering interpretation for #536; no study admission.

For MON-8, MON-12 and MON-13 (issue #536), treat frontend conformance as a controller-owned, versioned evidence binding rather than a caller-supplied completeness flag. A profile binds the exact binary and revision, matched launch/configuration identity, controller implementation and fixture digests, and an explicit inventory of admitted routes and counting units. Route qualification must be recomputed from retained source and execution/refusal evidence; matching hashes or a successful frontend exit alone do not qualify a route. Admission may retain a truthful partial profile for engineering work, but an explicit complete-coverage requirement refuses any unverified route or revision. Resume and postflight recheck the same profile and preserve its source evidence. Child tools, hosted tools, nested dispatch and continued process input remain explicit parts of the inventory; required tools must not be removed to obtain completeness. Profile qualification does not supply independent confinement review, operator/private-evaluator approval or permission for study collection.

Each profiled session snapshots the profile and its controller/fixture inputs
under the controller-owned `conformance/` evidence directory before launch. The
profile digest is checked before its declarations can select input reads. The
common envelope records the write-time artifact hashes and a separate profile
binding audit, including failed launches. Finalization replays the snapshot
against the admitted reference; the original source tree is not needed for
that replay. Source drift still fails the ordinary postflight check, even when
the retained snapshot remains valid. A `bindings_verified` result establishes
input identity only and must not be interpreted as route qualification.

Qualification receipts reuse `matched-session.json` and its retained
`admission.json`. A route's proof reference identifies the arm and session
record; a separate `proof_files` hash inventory binds its evidence bytes.
Acquisition checks both immutable record identities, the original logical
launch, every referenced artifact and editable-file snapshot, and the retained
controller bytes. The original session may use an observed-coverage template
profile: the profile pointer and coverage requirement are excluded from the
logical configuration digest, while executable, model, policy, runtime, and
budget inputs remain bound. Receipt acquisition does not qualify a route;
execution/refusal semantics and actual frontend source must still be audited.

Failures before the first harness attempt retain acquired source snapshots even
when no tool journal exists. Receipt acquisition preserves that diagnostic
evidence without treating the failed session as a completed execution.

For filesystem effect probes, conformance fixtures should use an admitted
editable deliverable as the deterministic target, reusing the controller's
authored/final byte snapshots. Qualification must bind the requested operation
to that target and expected change. An unchanged snapshot alone does not prove
refusal: it must accompany verified pre-execution refusal evidence and an
availability case demonstrating the operation's effect. This avoids introducing
arbitrary filesystem reads into qualification. Hosted fixture observations
remain evidence about the bound fixture, not independent production-provider
attestation.

Child token totals are cumulative per thread, with usage required for every
owned turn in a multi-agent session. Repeated cumulative updates are not added
again. Root completion waits for active descendants, and child usage cannot
substitute for missing root or follow-up-turn usage.

For hosted web search, reserve an allowance before sending the Responses
request and set `max_tool_calls` to the smaller of that allowance and an
existing requested limit. Serialize this exchange with local reservations.
Release unused allowance only after validating the entire completed response;
completed calls remain charged. A timeout, cancellation, malformed completion,
or reported limit breach retains the full allowance and retires the authority.
The dispatch journal retains the exact upstream request, response, allowance,
and settlement; replay checks those bytes against the provider journal.

The supported public-provider binding is
`hosted_tool_contract="openai.responses.max_tool_calls.v1"` at
`https://api.openai.com/v1`. Its pre-execution guarantee relies on the
[Responses API contract](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
The separate `aisle.fixture.responses.max_tool_calls.v1` binding is restricted
to an operator-owned loopback fixture. Neither label qualifies a subscription
backend or another provider. The implementation supports advertised web search,
preserves client tools, and refuses unknown hosted types, background requests,
and compressed requests under this contract. Actual-Codex fixture results test
this integration; they do not attest a production provider, loaded-code identity,
or independent confinement.

For the pinned Codex frontend, request attribution uses the canonical
`client_metadata["x-codex-turn-metadata"]` snapshot in the retained provider
request body. Codex reserves its session, thread, turn, and parent fields from
caller overrides; direct headers are compatibility projections (see
[the pinned metadata implementation](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/src/responses_metadata.rs)).
Qualification matches the exact dispatch frame to a unique retained provider
response, verifies canonical and flat metadata agree, and joins the request to
owned App Server turn and ancestry notifications. Agent-message text is never
an ownership source. A late child-completion notification may refer to a finished
parent only while the named, already-owned child remains active; this exception
does not authorize late tool execution or new children. These checks establish
attribution within the captured frontend lifetime, not independent confinement.

When the admitted App Server launch uses the owned provider relay and shared
dispatch budget, live telemetry records attempted tool lifecycles but does not
terminate the frontend merely because the observed attempt count exceeds the
ceiling. The dispatch controller must first record the pre-execution refusal.
The live report identifies dispatch enforcement, and postflight derives that
mode from the admitted launch rather than trusting the report. Malformed
telemetry still fails closed. Historical observation-only launches retain their
original live-count stop behavior.

Nested harness calls have different host runtime and App Server call IDs. Their
link is instead the controller-generated request ID returned in the tool result:
RPC replay retains each delegated callback and its completion text; qualification
matches that text to the owned pipe reply and the audited controller attempt,
including operation, arguments, success value, and result. Each request can bind
only one callback. A completed RPC alone cannot establish this link, and missing
completion results remain unresolved rather than being inferred from tool names.

A quota-refused controller MCP call may still produce an App Server
tool-completion notification describing failure. Qualification accepts that
notification only alongside the exact exhausted reservation and retained
DispatchRefused delivery: source identity and arguments must match, status must
be failed, result must be null, and the error must match the owned server's
authorization-unavailable response. It does not accept an unrelated transport
failure or a successful result as quota-refusal evidence.

Nested cancellation owns cleanup through completion: repeated cancellation must
not close the RPC evidence directory while a handler or receipt writer remains
active, and must not publish the host reference before the host is reaped. The
proxy and host runner defer cancellation during these cleanup stages and then
propagate it. Closed receipts establish cleanup ownership, not successful tool
execution or external confinement; interrupted dispatch remains charged under
the existing uncertain-delivery rule.

Harness qualification is per admitted operation. A successful `check` case does
not establish `run` availability, and a negative controller verdict is denial
evidence rather than a successful availability case. Each admitted harness
operation requires its own successful availability and before-execution quota
refusal evidence, while the denial matrix continues to retain and verify real
negative replies.

Collection acquisition has separate bounds: 16 GiB and 262,144 input files,
described by a profile of at most 64 MiB. Each input file remains limited to
64 MiB; each session allows 8 GiB and 65,536 indexed artifacts. An actual
monolithic run retained 308 MB of indexed inputs after only 3.08 seconds of
simulation, including image-bearing RPC frames. The session allowance provides
room for full T1 episode evidence, and the collection allowance covers paired
run cases alongside the route and fault matrix. Exceeding any bound still
refuses acquisition; these are storage bounds, not evidence of completeness.

Filesystem collections keep only their hash index resident and authenticate
each file on access. Snapshot retention copies one file at a time. A session
collector selects the authenticated artifact index and both authored/final
snapshots, leaving unindexed worker originals intact on disk without duplicating
them into the proof. Per-file, per-session and collection limits remain enforced
alongside canonical paths, exact inventories and original receipt identities.

An exhausted nested call may be followed by an exhausted hosted-tool allowance
on the next model request. The frontend can then report a failed turn or be
cancelled while its host closes. Retain the original failed-host status and all
closed source snapshots. Refusal qualification may replay this combination only
when the original provider exchange proves a terminal hosted refusal, the closed
RPC journal proves the nested refusal, and dispatch contains no uncertain delivery
or overlapping reservations. This verifies refusal evidence, not successful host
execution or the cause of its failure. Ordinary availability auditing continues
to reject the failed host.

Concurrent last-slot probes use that same refusal verifier when the combined
frontend closes after a hosted denial. They still require the original bound
script, RPC ordering that proves overlapping callbacks, successful completion of
the authorized command, an unchanged refused target, and a successful treatment
check. Provider delegation is joined through the verified hosted-refusal report
or the ordinary provider-prefix report, as applicable. Qualifying this fault
never changes a failed session into a successful host execution.

All fallback arm-tool CLI launches also pass Python's `-B` flag. A monolithic
check can import installed packages before checking the authored module; allowing
that interpreter to write bytecode creates undeclared runtime entries and causes
the postflight inventory check to fail. A real-import regression covers both arm
commands against a pristine temporary dependency without relying on preexisting
package caches.

Trusted Dora launches suppress Python bytecode writes and direct Numba's JIT
cache to the private run directory. Python's bytecode flag alone does not stop
Numba from creating a cache directory beside imported simulator modules. These
controls keep the bound runtime inventory unchanged; runtime verification still
checks all entries and does not exclude caches or replace a drifted receipt.

Runtime inventory verification overlaps at most four file reads and queues at
most 64 entries at once. A standalone profile of the 49,842-file engineering
runtime spent 180 of 197 seconds opening files. Streaming each hash through a
bounded buffer and overlapping opens addresses that cost without caching an
inventory or skipping a verification boundary. Results retain canonical order,
all modes and link targets, and every entry's before/after mutation check; all
reader threads are joined before returning or raising. The resulting receipt
must still equal the previously bound inventory exactly.

A virtual-environment interpreter is invoked through its declared entry point,
while its resolved executable remains the execution-policy identity. Validator
and controller admission bind the invocation and resolved installation startup
directories, including `pyvenv.cfg` or its absence. Resolving the invocation before
launch can select base-installation packages instead of the admitted environment.
The engineering run fixture therefore binds the whole virtual environment and
base installation, preserves interpreter entry points, and uses the standard
build-plus-episode rollout allowance with separate, equal-arm audit overhead.

The prepared conformance fixture sizes its enclosing tool deadline with 1,800
seconds of overhead for validation, worker/source preparation, and postflight
runtime verification and evidence retention, plus 600 seconds of frontend
overhead at the session level. The previous combined 600-second controller
allowance was already exceeded by approximately 690 seconds of preparation in
an actual typed run; the episode succeeded but the controller timed out during
shutdown and could not retain a qualifying result. Both arms receive the same
new deadlines, bound into a new profile before execution. These are aggregate
sizing allowances, not separate stage deadlines. The standard simulation and
worker timeouts, episode counts and tool counts remain unchanged. The failed
attempt remains historical evidence, and these engineering deadlines neither
amend the registered study budgets nor authorize collection.

Offline qualification uses the same separate per-file and per-session acquisition
bounds as the receipt reader. Replaying an authenticated run in a temporary
directory preserves its recorded guard-summary location as diagnostic metadata;
the audit recomputes every guard statistic from the indexed Arrow traces and
compares all measured values and relative trace names. A changed statistic cannot
be accepted by relocating the evidence or rehashing its summaries.

Nested fixture cells request an initial observation window equal to the admitted
tool wall deadline, rounded up to milliseconds. A prepared harness check took
61 seconds while the default cell observation yielded after 30 seconds; the
fixture's next response ended the session before the competing calls executed.
The explicit `@exec` pragma changes observation timing, not any execution budget.
The concurrency audit binds the provider's pragma-bearing script, the host's
pragma-stripped JavaScript source, and the matching RPC `yield_time_ms` field.
A yielded, unfinished cell remains insufficient evidence of the intended probe.

Native interruption and replay qualification preserve enabled optional routes.
An unused nested host may report cancellation or the owned app-server turn
failure only when its RPC inventory is empty, its proxy is clean, and the dispatch journal has no
nested reservation. Native delivery uncertainty remains charged and is audited
separately; it cannot invalidate an empty nested closure or become verified
execution. For interruption, every advertised hosted exchange must reconcile
against its reservation and contain zero completed hosted calls. Enabling the
tool is not evidence that it ran. Any actual hosted work, unrelated host failure,
hidden RPC activity, or altered forwarding boundary keeps the fault unqualified.

Controller outages keep optional routes enabled as well. Their qualifier first
matches the owned protocol failure against the declared unavailable, timeout,
or malformed-response case, then permits that exact failure in the unused host.
The unchanged grant, unused service request, rejected response where applicable,
zero controller executions, and uncertain charged delivery remain mandatory.

With hosted tools enabled, an exhausted allowance correctly prevents another
upstream request. Local-refusal fixtures therefore offer the final allowed call
and the excess effect in one response, before requesting another allowance.
The budget still reserves each local call separately and withholds the excess
call at its own boundary. An unused host's direct quota exception is accepted
only in refusal auditing; the source verifier must independently establish the
refused reservation. Model request counts never substitute for tool counts.

Child-route fixtures reserve spawn and frontend wait together in their first
provider response. Waiting inside the provider's HTTP handler would hold the
shared hosted allowance needed by the child's next request. The frontend wait
instead uses the admitted tool deadline, leaving the child's effect at attempt
three and its excess effect at attempt five under the common four-call quota.
Direct harness coverage remains in the harness cases; the child-harness cases
qualify the child's own controller call without an unrelated parent check.
