# Nested Code Mode admission

The matched App Server runner can own an external Codex Code Mode host and place
AISLE's RPC relay between that host and the frontend. This implements the nested
callback portion of MON-8/MON-12/MON-13 under issue #536. It does not close #536 or
#519: hosted tools, subagents, equivalent Claude routes, complete conformance and
independent confinement still need evidence. The optional
[provider admission relay](provider-admission.md) covers direct native calls and
continued input in the matched runner. No scored campaign is authorized by
these engineering checks.

Both arm launch bindings must declare the same `code_mode_host` object with an
absolute executable `path` and its unprefixed SHA-256 `sha256`. Both budgets must
include the same positive `frontend_tool_ceiling`. Admission and plan revalidation
check the executable digest; the runner checks it again before starting the host.
The controller starts a fresh host, binds the relay to its published loopback
endpoint, and supplies `--code-mode-host` and `features.code_mode=true` to the
owned frontend. It retains the ordinary tool catalog and does not enable
`code_mode_only`. The engineering protocol is pinned to Codex 0.153.4; the external
host must match that protocol. Binaries are operator-selected, not downloaded or
upgraded by a campaign.

Each Execute request fixes its host session, execution identity and tool catalog.
A callback must match that catalog, invocation/runtime identities, cell, sequence,
kind and argument encoding. The relay retains the exact RPC frame and obtains a
durable shared dispatch reservation before writing a native callback to the
frontend. The reservation's execution identity includes the host session, so a
fresh lease cannot collide with an earlier lease's reused runtime identifiers.
Quota refusal returns a failed completion to the host without forwarding the
callback. Cancellation and cell closure retire late callbacks without refunding
already reserved work.

Only exact namespaced `harness.check` and `harness.run` tools declared by the
launcher delegate admission to the existing App Server request/grant boundary.
That boundary reserves before controller execution. A nested harness call is
therefore charged once, alongside native callbacks in the same budget. A bare
lookalike name such as `harness.check` cannot impersonate that namespace.

The relay retains bounded, ordered RPC records under `code-mode/rpc`; its closed
reference is in `code-mode-reference.json`. The host's endpoint and stderr remain
under `code-mode/`. Postflight replays the session/execution lifecycle and binds
each native reservation to its exact callback frame. It partitions the shared
journal into verified native attempts and existing source/grant calls, requiring
all entries to be explained. Host callback IDs and App Server call IDs are
separate identities; the audit does not invent an equality between them.
Shutdown waits for RPC handlers and pending writes before closing evidence.
Host or relay failure cancels and reaps the owned frontend; there is no fallback
to an unmediated host.

Loopback addressing and an executable digest are not independent caller
confinement or concurrent-replacement evidence. The launcher/operator remains
responsible for process, filesystem and network ownership. Reports retain
`complete_coverage: false` and `confinement_verified: false`.

## Engineering checks

`tests/accept/test_code_mode_proxy.py` exercises the real gRPC relay, including
subscription headers, callback refusal and postflight replay.
`tests/accept/test_actual_app_server_authority.py::test_actual_mixed_nested_calls_share_controller_budget`
uses actual Codex and host binaries with a scripted localhost provider and the
real controller in each arm. At ceiling two, a native command and a harness check
execute and the third native call is refused. At ceiling three, all three execute.
Select binaries explicitly through `AISLE_CODEX_PROBE_BINARY` and
`AISLE_CODE_MODE_HOST_BINARY`. These tests require the macOS fixture sandbox and
make no live provider request. Unit tests cover identity drift, replay, early
cancellation, closure, completion correlation, host drift and process cleanup.

The protocol source, descriptor and license are packaged under
`aisle.harness._code_mode_protocol`; their provenance is recorded in that package.
CSE and BND successor registrations must bind the final controller/dependency
changes while inheriting existing seed commitments and preserving pending gates.
