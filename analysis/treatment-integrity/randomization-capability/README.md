# TRT-8 randomization and host-load capability audit

This directory retains synthetic, unscored evidence for the controller-side
TRT-8 mechanisms in SPEC 420. It is not a confirmatory campaign and does not
claim that either agent vendor path is sealed or ready for assignment.

## Primary record

`audit-name-hardened.json` is session
`randomization-capability-20260901T161037993298Z`. Its raw-file SHA-256 is
`71bdac920ddc4250a4d04f132a189cfaec6ca8fa65ae1bd232eb3cc1665f4312`.
The implementation used by the audit has SHA-256
`f5af4dc3fcc25ea2a4dcb675e42a48c183a0593ce60ee9b78d2cb3c81db59e58`.

The synthetic plan has two named arms and three temporal blocks. Each block has
one assignment per arm. The public preassignment object retains the algorithm,
arm set, block set, seed commitment, and a secret-salted plan commitment, but
not the seed or future arm order. All six assignments were then revealed in
sequence for the completed audit. The seven registered checks passed.

The same frozen sampling rule captured real preflight and postflight host-load
observations on Darwin/arm64. These observations were immediate capability
samples, not the boundaries of an agent session. A separate labeled synthetic
fixture changes normalized one-minute load from 0.25 to 1.5 and retains both
`HIGH_POSTFLIGHT_LOAD` and `LOAD_SHIFT`; the analyzer does not silently drop the
anomaly.

`audit.json` is the earlier successful development observation, retained rather
than overwritten. Its raw-file SHA-256 is
`eec36105a62e075dc58d23e657a0cd6ce697da269bb6fd67325749875f226676`.
It predates the fail-closed checks for scalar arm lists and delimiter-bearing
arm or block names, so it is not the primary record.

## Reproduction

From the repository root, use a fresh output path because evidence writes are
non-overwriting:

```bash
uv run python -m aisle.harness.treatment_randomization audit-synthetic \
  --output /tmp/aisle-randomization-audit.json
```

The synthetic assignment commitments and revealed order reproduce exactly.
Timestamps, temporary runtime conditions, and live load values can differ.

## Boundaries and remaining work

- The seed in this audit is synthetic. A confirmatory seed must be generated and
  preserved only in the hidden controller, with the public commitment frozen
  before collection.
- The Python controller object is not an operating-system access boundary and is
  not yet connected to a sealed Claude/Codex launcher.
- Live load samples are not a completed agent-session bracket. Confirmatory
  preflight and postflight launch integration is still required.
- The anomaly thresholds are capability-test values, not a frozen scientific
  exclusion rule. Final thresholds and handling belong in the protocol freeze.
- TRT-8, TRT-14, TRT-15, and issue #353 therefore remain open.
