# TRT-11 credential-scrub capability audit

This directory retains synthetic, unscored evidence for the controller-side
TRT-11 credential lifecycle in SPEC 420. It contains no real credential,
refresh token, vendor session, simulation run, or physical-robot observation.

## Baseline and primary record

The pre-implementation campaign helpers removed exact Claude and Codex
credential filenames, and several launchers used `finally` blocks. However,
most launch paths discarded the scrub result, no immutable postflight record
proved both canonical locations absent, and interrupted-path behavior was not
audited end to end. The tests-first baseline failed during collection because
`aisle.harness.treatment_credentials` did not exist. A later expected failure
showed that the primary audit artifact did not exist before execution.

`audit.json` is session
`credential-scrub-capability-20260901T171134346144Z`. Its raw-file SHA-256 is
`a1c36e95f156a71fea713fa469088eac4809ee3b220448e5c6aa5318211028c4`.
The implementation used by the audit has SHA-256
`30a8c400a43a42b44be08389db77bcfc49722f1e88713464ef056e1c49f3d941`.

All eleven registered checks passed. The audit covers normal cleanup, both
canonical locations, secret-free proof, an interrupted session, an aborted
auth-probe launch, preservation of same-named non-secret evidence, safe symlink
unlinking, refusal of a symlinked credential parent without deleting the
external target, explicit exclusion of an unexpected directory at a
credential path, already-absent proof, and non-overwriting evidence
publication. It also records the exact canonical-location configuration and
runtime environment.

`audit-pre-parent-hardening.json` is the earlier successful development
observation, retained rather than overwritten. Its raw-file SHA-256 is
`4d63fcadb078519c648e4694d2928238a8a917ec282d5c879ddffecdf02e1cfa`.
It predates the parent-directory symlink refusal and the clearer
`credential_bytes_in_record` field, so it is not the primary record.

## Reproduction

From the repository root, use a fresh output path because evidence writes are
non-overwriting:

```bash
uv run python -m aisle.harness.treatment_credentials audit \
  --output /tmp/aisle-credential-scrub-audit.json
uv run pytest tests/unit/test_treatment_credentials.py -q
```

Timestamps and session identifiers will differ. Check names, counts, and
pass/fail results are deterministic for the same implementation.

## Boundaries and remaining work

- Only synthetic credential sentinels were used. The report intentionally
  retains states and error classes, never credential contents or content
  hashes.
- The guard is not wired into every Claude/Codex auth-probe and session
  launcher. Existing cleanup calls therefore do not yet have uniform immutable
  postflight proof.
- A real vendor refresh-token rewrite was not exercised. Such a test must
  preserve proof of canonical absence without archiving the token.
- The proof must be included by the TRT-10 archive before cleanup of actual
  session outputs.
- TRT-11, TRT-14, TRT-15, and issue #353 therefore remain open.
