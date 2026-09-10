# Provider call admission

The matched App Server runner can place an owned HTTP relay between Codex and
its Responses API provider. This adds direct native function calls, custom tool
calls, and client-side tool search to the shared reservation boundary in
MON-8/MON-12/MON-13, issue #536. Both arms use the same relay, budget and evidence
format. It does not authorize scored collection or establish complete coverage.

Both App Server launch bindings may declare the same `provider` object:

```json
{
  "base_url": "https://api.openai.com/v1",
  "requires_openai_auth": true
}
```

`base_url` is the fixed upstream base, without credentials, query or fragment.
HTTPS is required except for an operator-owned loopback HTTP fixture.
`requires_openai_auth` defaults to `true`; the scripted local acceptance provider
explicitly selects `false`. The runner supplies an owned localhost Responses
endpoint, disables WebSocket transport and retries, and keeps the declared model,
ordinary tools, approval policy and outer confinement wrapper. It forwards
provider authentication to the fixed upstream without recording the header.
It does not follow redirects. Other provider protocols and credential schemes
need their own bound adapters; this object is not a generic forward proxy.

Admission requires the same positive `frontend_tool_ceiling` in both arms.
The provider source, relay, runner and auditor are included in the controller
fingerprint. Frontend binary/configuration verification and independent network,
process and filesystem ownership remain separate requirements. A loopback URL
does not establish caller authentication or prevent alternate provider access.

## Execution and counting

The relay acquires a bounded complete SSE response before releasing its events.
It verifies the response identity, unique item/call identities, added/done item
identity, completed output inventory and terminal event. It preserves exact
wire bytes. This buffering changes streaming latency equally in both arms; the
session wall ceiling still applies. Each request or response is bounded at
16 MiB, total retained provider evidence at 64 MiB, and concurrent handlers at
eight. Invalid, oversized, incomplete or replayed streams retire the relay.

For the pinned Codex 0.153.4 implementation, a completed output item can start
its tool handler before `response.completed`. The relay therefore obtains and
flushes a separate reservation immediately before forwarding each executable
`response.output_item.done` event. Authorizing only one call and then forwarding
an entire multi-call response is insufficient. On refusal, previously consumed
reservations remain consumed and the remaining response is not released.
A byte writer returning does not prove that an authorized call executed.

The counted provider unit is one function call, custom tool call, or client-side
tool-search call. This includes MCP dispatch, a native edit and each continued
`write_stdin` invocation as separate calls. Counting binds the exact namespace,
name and payload kind; a dotted lookalike is not a namespaced harness tool.
Response IDs and call IDs cannot be replayed during the relay lifetime.

Only declared `harness.check`/`harness.run` function calls delegate their charge
to the existing App Server source/grant/controller boundary. With an owned
Code Mode host, its exact `exec` custom-tool wrapper delegates to nested callback
admission. Native provider items, native host callbacks and harness requests use
one shared dispatch journal. The layers never add overlapping observation
counts or charge a delegated call twice. The existing observed-event guard stays
active.

The runner watches relay failure while the frontend runs. Failure cancels and
reaps the frontend and owned host; there is no retry through an unmediated
provider. Accepted sockets are owned before HTTP parsing finishes, handlers are
joined before evidence closes, and acquired response prefixes survive timeouts.
Failures remain infrastructure exclusions with available evidence retained.

## Evidence and checks

The controller retains exact request bodies, response SSE bytes and ordered
delivery records under `provider/`; `provider-reference.json` holds the closed
write-time reference. Authentication headers are excluded, but request bodies
contain the session's ordinary prompt/tool context and belong in the same
protected evidence boundary as the transcript. Postflight acquires bounded
snapshots and checks byte identity against that controller-held reference.

The provider auditor replays each response and binds native executable frames
to unique dispatch entries. Provider harness call IDs, names and arguments must
match the owned frontend source messages before the existing grant → request →
controller-attempt checks. When a host is present, its callback identities remain
a separate domain; the auditor does not invent equality with provider IDs.
Provider, nested-native and harness dispatch subsets must be disjoint and explain
the whole journal. A changed/missing source, reused reservation, uncertain
delivery or failed relay cannot become successful admission evidence.

`tests/accept/test_actual_app_server_authority.py` uses the actual pinned Codex
binary and a scripted local provider in each arm. Cases cover native → harness →
native calls, multi-call responses, MCP, freeform editing, continued process input,
and the combined provider/Code Mode path. Success cases prove route availability;
quota cases prove refusal before the excess side effect. Select binaries with
`AISLE_CODEX_PROBE_BINARY` and, for nested cases, `AISLE_CODE_MODE_HOST_BINARY`.
Captures bind the binary revision, fixture inputs and launch configuration.
No paid inference or physical device is used by these fixtures.

`tests/accept/test_provider_relay_transport.py` covers real HTTP delivery,
semantic evidence replay/tampering, incomplete-client shutdown and provider
timeouts. Unit checks cover stream identity, concurrent reservations, replay,
namespace/payload-kind delegation, uncertain writes, process cleanup and the
provider-to-frontend source link.

## Remaining gates

These are engineering checks for a pinned software path, not a complete
frontend conformance certificate. Hosted work may already execute upstream;
observing its result does not establish pre-execution control. Subagent-owned
transports, other frontend revisions/configurations and equivalent Claude routes
still need verification. No required tool is silently removed to obtain passing
checks. `complete_coverage` and `confinement_verified` remain `false`, and any
admission requiring complete coverage stays refused. Issues #536 and #519,
independent reviews, operator/private-evaluator evidence, physical evidence and
study-collection gates remain open.
