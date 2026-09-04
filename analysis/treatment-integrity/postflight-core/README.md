# Synthetic treatment postflight

This directory retains an **unscored synthetic** TRT-9 postflight and an
unreadable-audit negative control. These records validate the postflight
mechanism; they are not campaign sessions, confirmatory results, or evidence of
physical-robot behavior.

The primary v2 record is `postflight-synthetic-pass-v2.json` (raw SHA-256
`c33e79280e8e681ce3787c764b18e0520984be4c30def290ca22b59d0b1b9c2e`).
Its content identity is
`sha256:2fc2bb75deaf70c45f3e3ce7fb6f5d8f4efd1d886eaf40640e2a76151ceed2d8`.
It recomputes the preserved preflight treatment identity and visible-file
hashes, records a complete active-adapter log with two denied hidden accesses
and no hidden exposures, and reports `synthetic_pass`. The record explicitly
sets `confirmatory_ready` and `eligible_for_estimate` to `false`.

The v2 negative control is `postflight-unreadable-log-v2.json` (raw SHA-256
`0d57084aa271e14003392b178566ab54533c0e2b557a959d141799a8e36b6157`).
The requested audit path does not exist, so the CLI exits 3 after retaining an
`infrastructure_exclusion` with reason `hidden_access_log_unreadable`.

Reproduce the positive record from preserved inputs with:

```console
uv run python -m aisle.harness.treatment_postflight create \
  --preflight analysis/treatment-integrity/manifest-core/preflight.json \
  --candidate analysis/treatment-integrity/manifest-core/candidate.json \
  --root analysis/treatment-integrity/manifest-core/visible \
  --hidden-access-log analysis/treatment-integrity/postflight-core/hidden-access-log.json \
  --output /tmp/aisle-postflight.json
uv run python -m aisle.harness.treatment_postflight verify \
  --postflight /tmp/aisle-postflight.json
```

The earlier `postflight.json` and `postflight-unreadable-log.json` development
observations are preserved rather than silently discarded. They use the v1
schema from before the scientific-eligibility safeguard and are invalid for
claims or estimates: v1 used the ambiguous label `eligible` for a synthetic
pass. The v2 verifier rejects v1 records, and only the v2 files above are the
review targets.

Remaining TRT-9 work is integration with actual Claude and Codex session
launchers and protocol-frozen confirmatory inputs. Until that integration and
TRT-15 freeze exist, no postflight produced by this implementation can become
confirmatory-ready or estimate-eligible.
