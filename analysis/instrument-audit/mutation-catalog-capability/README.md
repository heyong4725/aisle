# AUD-3/AUD-5 mutation-catalog capability

This directory retains the current fail-closed mutation-catalog gap for SPEC
430. It is validation evidence only: no production instrument was mutated and
no independent reviewer signed an audit.

## Current gap

The tests-first baseline failed because `aisle.harness.instrument_mutations`
did not exist. `current-catalog.json` then records the actual current state: no
catalogued production mutations. Its raw SHA-256 is
`c9aa4d5dbcb77c428b2d1c56f77bf1b983aa0a7e51a392cd2cc9e6e7cf51afd1`.

`current-gap.json` is session
`instrument-catalog-20260901T224341625442Z`, raw SHA-256
`dcfd1d7bb2b366ee4319920bd09c9a9dff7d49b1bf38d3ddad24806c50690ed8`.
The validator source SHA-256 is
`9c0d96245ebdbcb10ff7d5de44246b1b8147ad4cecefc41228ee0c12a003d8ea`.
It reports `catalog_valid: false`, `publication_gate: blocked`, the invalid
AUD-1 inventory dependency, an empty catalog, and all 16 required coverage ids
as uncovered: two primary targets, four exclusions, and ten categories.

## Capability demonstrated

Synthetic frozen fixtures verify that a catalog is valid only when it
mechanically covers every inventory id and each mutation has a unique stable
id, content-bound fixture, exact operator, severity/rationale, case-scoped
evidence paths, resolvable target, fixed expected detection layer and
comparison, and a content-bound hand derivation or independent implementation
that shares no production helpers. Wrong-layer, unknown, duplicate, unsafe,
hash-drifted, production-derived, or uncovered rows fail closed.

## Reproduction

The current-gap command is expected to exit 2:

```bash
uv run python -m aisle.harness.instrument_mutations \
  --inventory analysis/instrument-audit/inventory-capability/current-inventory.json \
  --catalog analysis/instrument-audit/mutation-catalog-capability/current-catalog.json \
  --project-root . \
  --output /tmp/aisle-current-catalog-gap.json
uv run pytest tests/unit/test_instrument_mutation_catalog.py -q
```

## Remaining work

AUD-3 remains incomplete until frozen production coverage exists. AUD-4 still
needs isolated one-mutation execution, and AUD-6 through AUD-14 remain open.
AUD-12 requires review outside the affected instrument authorship chain.
