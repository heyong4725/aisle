# Claim-catalog audit evidence

These are machine-readable outputs from the SPEC 410 checker for the catalog
and generated matrix committed in the same change.

- `catalog-audit.json` is the successful ordinary development audit from
  `uv run python tools/claim_evidence.py --check`.
- `release-readiness.json` is the expected nonzero result from
  `uv run python tools/claim_evidence.py --check --require-release-ready`.

Both outputs name the same generated-matrix SHA-256. The release gate fails
only because `CLM-12` has no independent signed terminology-review record.
That is a real external-review dependency, not a unit-test, simulation, replay,
synthetic-fault, or physical-robot observation. These files must be regenerated
when the catalog digest or release-review state changes.
