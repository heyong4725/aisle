# ADR-63: reserve covered frontend calls before delivery

Status: PROPOSED — engineering interpretation for #536; no study admission.

For MON-8/MON-12/MON-13 and issue #536, a controller-owned reservation counts one
normalized `(turn_id, call_id)` within its bound session before releasing that
call's bytes. An independent adapter must authenticate the identity, bind it to
the parsed frame, and provide a bounded byte writer. A stdout event, a
participant-provided ID, or an eventual response-completion event is insufficient:
actual Codex probes execute a direct tool call before response completion, and
both Codex and Claude command-hook probes allow execution on several hook failure
paths. This layer does not change the existing observed-event counting unit.

`DispatchBudget` owns a fresh authority directory and serializes decision
retention and delivery. It retains each bounded frame and authorization/refusal
before invoking the writer, flushing files and directory entries. Replayed call
identities cannot obtain a second delivery. A reserved call stays charged even
when delivery is cancelled, partially fails, or its terminal receipt cannot be
retained; uncertainty closes the authority. A new controller cannot reopen the
same directory at zero usage. The caller must retain the resulting session
invalidation and follow the admitted recovery policy rather than creating an
untracked replacement authority.

A `returned` delivery receipt means only that the writer returned. It is not a
frontend execution or outcome attestation. The authority declaration always
leaves complete coverage and confinement unverified. The current library and
actual-CLI acceptance fixture establish this reservation boundary for covered
calls; they are not yet wired into matched campaign admission. Full authenticated
frontend-to-controller linkage, complete route coverage (including hosted and
nested work), shared admission for both arms, resource/deadline closure and all
independent study gates remain required. Neither #536 nor #519 is closed by this
component.

The acceptance fixture uses a local scripted provider and the installed Codex
CLI. A two-call control executes both markers; at a one-call ceiling, or after
injected failure to retain the second reservation, the first command completes
and the second marker never appears. It checks raw process/results and side
effects separately from the helper's single-call probe report. Run explicitly on
macOS with the selected binary:

```bash
AISLE_CODEX_PROBE_BINARY=/absolute/path/to/codex \
  uv run pytest tests/accept/test_real_frontend_dispatch.py
```

These fixtures are engineering evidence, not live model/provider conformance or
campaign outcomes. The test skips when the required platform or explicitly
selected CLI is absent; such a skip supplies no acceptance evidence.

`verify_dispatch_journal` checks immutable byte snapshots against a separately
retained controller reference containing session, ceiling, attempt/reservation
counts and the complete artifact digest map. Acquisition and authentication of
that reference belong to the caller; participant-supplied matching hashes cannot
establish identity. Replay checks exact artifact membership, normalized call
identities, ceiling/replay decisions, frame digests and delivery transitions.
Missing or contradictory records fail verification. A consistent uncertain
terminal delivery remains explicitly uncertain even when the evidence audit is
`ok`; this is not permission to continue the session. The caller supplies the
verification byte limit and must also bound snapshot acquisition. This verifier
does not yet connect a frontend call to the service/controller chain or authorize
matched campaign admission.
