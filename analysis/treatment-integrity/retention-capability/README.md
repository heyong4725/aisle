# TRT-10 retention capability audit

This directory retains synthetic, unscored evidence for the controller-side
TRT-10 retention mechanism in SPEC 420. It is not a confirmatory campaign and
does not claim that either agent launcher currently enforces this cleanup
barrier.

## Baseline and primary record

The pre-implementation campaign runner retained `session.jsonl`,
`session.stderr`, `token_samples.jsonl`, and a Git deliverable reference, but it
did not publish one verified archive containing every TRT-10 evidence class or
provide an estimate gate that rejected incomplete retention. The tests-first
baseline failed during collection because `aisle.harness.treatment_retention`
did not exist. A second expected failure showed that no primary audit artifact
existed before the capability run.

`audit-schema-hardened.json` is the primary record, session
`retention-capability-20260901T164249923291Z`. Its raw-file SHA-256 is
`a4a9b437b8bbdea2a12181e3373ea9553d235fd024c448e8bfb7b4d919cda73e`.
The implementation used by the audit has SHA-256
`1ce69fdcdc24ef82908e3412bc9a8f2382c2eca1450ca9c2b5eeaf52cb97f8bb`.

The audit exercised nine checks. It retained all eleven required evidence
classes, independently verified every file and directory entry, detected byte
corruption, refused a missing source before publication, refused overwrite,
retained a randomized assignment whose launch was refused, rejected both that
assignment and a synthetic started session from estimation, and left no
staging directory after atomic publication. All nine checks passed.
The record also preserves the exact archive configuration, an explicit
not-applicable randomization-seed status, and the operating-system, machine,
Python implementation, and Python version used for the audit.

`audit-initial.json` and `audit-environment-bound.json` are earlier successful
development observations, retained rather than overwritten. Their raw-file
SHA-256 values are, respectively,
`fae8d7e5f8975af62f7bec8c8c9cf1b87ff946741b2cf10b1fb4c5d6e3033a6a`
and `fd8e76a5e181187cbd391a6f16c342f3da6a345aa9b15cbc0ab43271800c4920`.
The first predates explicit environment/configuration metadata; the second
predates fail-closed malformed-entry handling. Neither is the primary record.

## Reproduction

From the repository root, use a fresh output path because audit writes are
non-overwriting:

```bash
uv run python -m aisle.harness.treatment_retention audit \
  --output /tmp/aisle-retention-audit.json
uv run pytest tests/unit/test_treatment_retention.py -q
```

Timestamps and session identifiers will differ. The check names, counts, and
pass/fail results are deterministic for the same implementation.

## Boundaries and remaining work

- The sources and assignments are synthetic. No Claude, Codex, simulation, or
  physical-robot session was executed.
- The mechanism is not yet connected to either agent launcher, so it does not
  authorize cleanup of a real session worktree.
- Actual campaign integration must create and retain the preflight,
  postflight, tool/audit, randomization, tool-policy, exclusion, and complete
  deliverable/idea sources before invoking the archive barrier.
- Analyzer paths must call the estimate gate on every candidate session.
- TRT-10, TRT-14, TRT-15, and issue #353 therefore remain open.
