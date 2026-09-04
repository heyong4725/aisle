# AUD-1/AUD-2 instrument-inventory capability

This directory retains a fail-closed inventory-validation observation for
SPEC 430. It is not an instrument mutation audit, an independent review, a
protocol freeze, a confirmatory campaign, or physical-robot evidence.

## Baseline and retained current gap

Before this slice, AISLE had strong instrument-specific tests but no common
machine validator that required every frozen primary estimand, exclusion rule,
and decision-bearing instrument category to be content-bound and mapped. The
tests-first baseline failed during collection because
`aisle.harness.instrument_audit` did not exist. A later expected failure showed
that the retained current-gap report did not exist before execution.

`current-inventory.json` is an explicit snapshot of the current unfrozen
state. It names the two central causal claim targets, four currently specified
exclusion families, and all ten AUD-2 categories without promoting any of them
to frozen or covered. Its raw SHA-256 is
`cd802c1479afdee9d5aaa731ae2d29a411e188e8c1adf38f0e4789a83faf58fa`.

`current-gap.json` is session
`instrument-inventory-20260901T222106411763Z`. Its raw SHA-256 is
`56d5d66f0c33aaec3c4b730c1af77952c533bd2f2099a795728b40b2f7e85ec6`.
The validator source used by the report has SHA-256
`e6cae162dfa73e96829d144691b8db4e94ce25f2b4b5407d44c7ac841e7930ed`.
The record also binds the proposed statistics specification and claim catalog
by SHA-256.

The retained result is deliberately `inventory_valid: false`,
`publication_gate: blocked`, and `confirmatory_ready: false`. It reports 28
specific gaps: both named primary targets and all four exclusion families are
unfrozen and uncovered; all ten instrument categories are unresolved; neither
protocol record is frozen; the global freeze is unresolved; and no audited
instrument entries exist. No missing item is silently dropped from a rate.

`current-gap-pre-authorship-hardening.json` is the earlier development
observation, retained rather than overwritten. Its raw SHA-256 is
`96b740d7d43339ffc842a56536b885d79e15ee679757296adc63f38d48f52b4f`.
It predates bound-record authorship cross-checking, full path-component symlink
refusal, and protocol-id validation for not-applicable citations, so it is not
the primary record.

`current-gap-pre-entrypoint-resolution.json` is the next development
observation. Its raw SHA-256 is
`bbd711227fcf9a645c40f2422bdb3ea70402818b907532856daab81a02d0d917`.
It predates mechanical resolution of Python callables and CLI entrypoints and
is also not the primary record.

## Capability demonstrated

The synthetic unit fixture demonstrates that the validator can accept a
complete frozen inventory while still keeping publication blocked pending
AUD-3 through AUD-12. Negative fixtures independently exercise every required
coverage id and enforce:

- exact coverage of ten decision-bearing instrument categories;
- non-empty frozen primary-estimand and exclusion-rule registries;
- protocol, implementation, and authorship-record content hashes;
- implementation entrypoints, source schemas/fields, and output fields;
- mechanical resolution of a declared top-level Python callable or CLI module/
  file entrypoint to the content-bound implementation path;
- resolvable upstream/downstream instrument topology;
- reviewable protocol citation and rationale for any category marked not
  applicable; and
- refusal of duplicates, unknown coverage, unsafe paths, hash drift, or
  unresolved status.

## Reproduction

The current-gap command is expected to exit 2 because the retained inventory is
invalid by design. Use a fresh output path because reports are non-overwriting:

```bash
uv run python -m aisle.harness.instrument_audit validate-inventory \
  --inventory analysis/instrument-audit/inventory-capability/current-inventory.json \
  --project-root . \
  --output /tmp/aisle-current-instrument-gap.json
uv run pytest tests/unit/test_instrument_inventory.py -q
```

Timestamps and session identifiers will differ. The gap set and coverage
counts are deterministic for the same input and bound files.

## Boundaries and remaining work

- The two causal targets and four exclusion families are a visible planning
  snapshot, not frozen estimand wording or a complete catalog.
- No production instrument has yet been entered, mutated, or independently
  recomputed under SPEC 430.
- AUD-3 through AUD-11 still require the mutation catalog, independent
  oracles/recomputations, runner, accounting, and blocking audit.
- AUD-12 requires a reviewer outside the affected instrument authorship chain;
  repository-owner or coding-agent review cannot satisfy it.
- Issue #354 therefore remains open and confirmatory collection remains
  unauthorized.
