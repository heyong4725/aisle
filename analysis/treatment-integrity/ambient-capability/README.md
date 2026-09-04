# TRT-7 ambient-state capability audit

This directory retains synthetic, unscored evidence for the ambient-state
boundary in SPEC 420. It is not a Claude/Codex campaign and cannot support a
treatment-effect or confirmatory-readiness claim.

## Primary record

`audit-provenance-complete.json` is the primary record. Session
`ambient-capability-20260901T153905193096Z` ran one real child process on
Darwin/arm64 with Python 3.13.5. All seven isolation checks passed and the one
declared-PATH false-alarm control passed, for detection rate 1.0 and false-alarm
rate 0.0. The record's raw-file SHA-256 is
`dec81e1cc808b0568a22f72ff49f1f7f52ba4f20af33634bca13d7d3ec46fed2`.

The audit covers fresh HOME, Claude/Codex config homes, XDG data/state/cache,
temporary directories, unrelated and credential environment variables,
environment-disclosed socket endpoints, and one deliberately inheritable file
descriptor. The implementation source used by the audit has SHA-256
`13dce400a649bc0d01749661517138f8ad1f8cde106caceaedb9f33af6f6d856`.
Synthetic sentinel and credential bytes are not retained in either JSON file.

## Development record

`audit.json` is the earlier successful development observation, retained rather
than overwritten. Its raw-file SHA-256 is
`2ad0f91a73f8d4d607c474bb649a4f86939119c078d31cac3cb6c1bedf43d8db`.
It predates the explicit platform, randomization-applicability, and
implementation-hash fields, so it is not the primary record.

## Reproduction

From the repository root, choose a new output path because evidence writes are
non-overwriting:

```bash
uv run python -m aisle.harness.treatment_ambient audit-synthetic \
  --output /tmp/aisle-ambient-audit.json
```

Then compare the generated JSON fields and raw hash. Timestamps and temporary
paths intentionally differ between sessions.

## Boundaries and remaining work

- This is a synthetic process capability check, not a vendor-authenticated
  Claude or Codex session.
- Removing environment-disclosed socket addresses does not prevent discovery or
  access through the filesystem or network; the external confinement adapter is
  the independent control for those channels.
- Existing controller credential-file seeding and scrubbing remain separate
  TRT-11 mechanisms.
- Equivalent vendor-path conformance, real launcher dry runs, retention,
  mutation coverage, and protocol freeze remain open. Therefore TRT-7, TRT-14,
  TRT-15, and issue #353 remain open.
