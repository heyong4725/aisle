# Frontend request authorization — engineering scope

Issue #536, MON-8/MON-12/MON-13. Codex App Server check/run requests now have
a controller-owned authorization path and a recomputable source-to-attempt
chain. This does not establish complete frontend coverage or authorize study
collection. The existing CLI transport remains available with its explicitly
partial observations.

## Admission and execution

An App Server launch declares `argv`, `tool_python`, and an `app_server` object
containing `baseInstructions` and `developerInstructions`. Admission checks the
actual prompt bytes against the existing system/research-contract hashes,
including the declared representation-specific document bundle. It also checks
the frontend binary, interpreter, shared arguments and budgets. The new runner,
transport, authority and auditors are included in the controller fingerprint.

The runner supplies the declared model and approval policy, uses an ephemeral
thread, and retains the exact thread configuration. Frontend sandbox mode is
`danger-full-access` inside the existing separately verified outer confinement
wrapper. That mode does not replace or attest the outer confinement gate.

The controller owns the frontend's stdin/stdout pipes. It initializes the
thread, records the returned thread/turn identities, and accepts only bound
`item/tool/call` requests for the declared `harness.check`/`harness.run` tools.
The source message is retained and flushed before dispatch. Foreign identities,
replay, malformed messages and unsupported server requests fail the session.
Native tool notifications remain observations rather than authorization.

For each dynamic harness call, the trusted runner issues a single-use capability
for exact newline-delimited request bytes. The grant is flushed before release.
The tool service flushes the raw request and its consumption receipt before
calling the controller. Altered requests and unissued or consumed grants cannot
start another authorized attempt. The frontend receives the controller-owned
response, not bytes read back from the participant-writable response channel.

When the admitted plan declares `budget.frontend_tool_ceiling`, the controller
also reserves each covered harness call before issuing its request grant. The
reservation binds the normalized call and exact source frame, is flushed before
delivery, and is never refunded after uncertain delivery. A replay or exhausted
reservation budget cannot issue another grant or reach another controller attempt;
failed retention closes the authority. The observed-event guard remains active.
These are separate checks against the declared upper bound, not additive totals
or complete enforcement of native frontend routes.

## Evidence and accounting

On closure, the authority returns hashes recorded when receipts were durably
written. The transport likewise returns write-time hashes of its protocol
messages. Expected hashes are not rebuilt from the evidence being audited.
These references remain in the session controller and are retained separately;
they are not external signatures or independent-review attestations.

Postflight acquires bounded snapshots, verifies their physical bytes, and checks
the controller attempt journal, grant/consumption/request links, and the owned
pipe's thread setup, source calls and replies. App Server sessions require this
source audit: grant-only evidence cannot silently satisfy it. Missing, changed,
duplicate or unmatched records produce an infrastructure exclusion. The common
record indexes the authority/protocol artifacts and their references.

For a declared ceiling, postflight also requires the closed dispatch authority's
write-time reference and verifies the reservation → source call → grant → request
→ controller-attempt links. A different ceiling, frame, call, missing reservation,
or uncertain delivery cannot pass as a successful session. The common record
indexes the dispatch artifacts and reference. Failed sessions retain available
transcript and observation diagnostics even when reservation-reference retention
fails; such evidence does not become a successful audit.

Token accounting uses cumulative App Server usage updates, counts new input
plus output once, and retains output tokens separately. Missing or inconsistent
usage cannot become a zero-token success. Normal completion requires a clean
frontend exit; timeouts and failures terminate the owned process group. Primary
failures remain primary if subsequent evidence retention also fails.

Frontend observations, authorized requests and controller attempts are separate
measurements. They are never added into an overlapping total. Historical
observations retain `complete_coverage: false`.

## Validation and remaining gates

Unit coverage includes exact request binding, concurrent/replayed consumption,
write-time references, altered/missing source records, participant response
replacement, invalid accounting, protocol failure, process cleanup and both-arm
runner/postflight integration. Actual pinned Codex acceptance uses a scripted
local provider and the shared production helper in both arms, including an
unavailable controller, quota exhaustion, and reservation/grant/delivery-retention
failures. The quota fixture suppresses the reactive observation stop to prove that
source-call admission independently refuses excess work. Captures record the
actual binary revision and hashes of the fixture inputs. Separate delivery tests cover
altered/unissued grants. These are engineering tests, not paid model inference,
treatment outcomes or independently reviewed confinement evidence.

The full admitted surface still needs verified pre-execution control paths:
native commands/edits, nested calls, hosted tools, subagents and continued input,
as well as equivalent supported-frontend integration. This change does not remove
those tools or reinterpret partial observations as complete enforcement. Issue
#536, #519 and independent confinement/operator/private-evaluator/physical-evidence
and study-collection gates remain open.

Protocol reference: [official App Server documentation](https://learn.chatgpt.com/docs/app-server).
Compatibility work used the pinned binary's generated experimental schema for
`DynamicToolCallParams` and namespaced `DynamicToolSpec`; online documentation
alone does not attest a pinned frontend revision.

The optional [Code Mode host binding](../../monolithic/code-mode-admission.md)
adds native nested callback reservations to the same controller budget. Its RPC
journal is replayed separately, then every shared dispatch entry must belong to
either that verified native subset or the existing App Server source/grant chain.
It preserves the incomplete-coverage and independent-confinement gates.
