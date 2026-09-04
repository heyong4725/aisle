# Treatment-manifest core evaluation

This directory is a **synthetic, unscored preflight fixture** for SPEC 420
TRT-1/TRT-2. It demonstrates deterministic manifest construction and refusal
behavior only. It is not an agent session, a confinement result, a confirmatory
campaign, or physical-robot evidence. The binary and model identities in the
candidate are synthetic values.

Regenerate the retained manifest from the preserved input and visible tree:

```bash
uv run python -m aisle.harness.treatment_integrity create \
  --candidate analysis/treatment-integrity/manifest-core/candidate.json \
  --root analysis/treatment-integrity/manifest-core/visible \
  --output analysis/treatment-integrity/manifest-core/preflight.json
uv run python -m aisle.harness.treatment_integrity verify \
  --manifest analysis/treatment-integrity/manifest-core/preflight.json \
  --root analysis/treatment-integrity/manifest-core/visible
```

The create command refuses to overwrite `preflight.json`. Remove only that
generated file before deliberately regenerating it. The focused unit suite
contains the missing-field, placeholder, invalid-hash, inconsistency, secret,
unsafe-path, drift, and overwrite refusal fixtures.
