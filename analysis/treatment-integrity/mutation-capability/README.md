# TRT-13 treatment-component mutation audit

This directory retains synthetic, unscored evidence for the SPEC 420 TRT-13
mutation gate. It is not an agent session, a confirmatory campaign, or
physical-robot evidence.

## Baseline and primary record

Before this slice, the preflight and postflight implementations had focused
negative tests, but there was no exhaustive report that independently removed
and drifted every component in the authoritative TRT-1 inventory. The
tests-first baseline failed during collection because
`aisle.harness.treatment_mutations` did not exist. A later expected failure
showed that the retained primary audit did not exist before execution.

`audit.json` is session
`treatment-mutation-20260901T174205798050Z`. Its raw-file SHA-256 is
`63787956ab2826a790bf99f3dd4055863938e6830794632c40438e03fc413260`.
The mutation-audit implementation used by the record has SHA-256
`7bafd4fd4f75c584a5b4314b77cdf468f4d638e43753b13118352e0676891eec`.
The report also binds the preflight and postflight detector implementations
and the source fixture by SHA-256.

The authoritative inventory contains 40 TRT-1 component paths. The exhaustive
audit ran 80 independent critical mutations: 40 missing-component mutations
and 40 post-preflight drift mutations. All 80 were detected, for an overall
detection rate of 1.0 and a per-kind detection rate of 1.0. Missing components
were refused at preflight and drifted components were classified as
infrastructure exclusions at postflight. No critical blind spot survived.
Both the unchanged-treatment control and a controller-private file outside the
agent-visible root passed without a false alarm, for 0 false alarms in 2
controls.

The audit owns an explicit inventory derived from SPEC 420 rather than
discovering its cases from the detector under test. It refuses to run if that
independent inventory and the detector inventory diverge. This prevents a
deleted detector requirement from silently deleting its own mutation case.

`audit-detector-derived-inventory.json` is the earlier successful development
observation, retained rather than overwritten. Its raw-file SHA-256 is
`5ef78d50ffdb0f443ed0976077bc7fb03554601b9b5978432d06841893756c4a`.
It predates independent inventory ownership and is not the primary record.

## Reproduction

From the repository root, use a fresh output path because audit writes are
non-overwriting:

```bash
uv run python -m aisle.harness.treatment_mutations audit \
  --output /tmp/aisle-treatment-mutation-audit.json
uv run pytest tests/unit/test_treatment_mutations.py -q
```

The timestamp and session identifier will differ. The component inventory,
mutation identifiers, dispositions, counts, and rates are deterministic for
the same bound preflight/postflight implementations and fixture.

## Boundaries and remaining work

- The audit uses synthetic manifest and visible-tree fixtures. It executes no
  Claude or Codex vendor session and incurs no agent budget.
- It mutates every component in the authoritative TRT-1 manifest inventory; it
  does not mutate operating-system confinement behavior or live vendor paths,
  which are covered by separate TRT-3 through TRT-7 and TRT-14 work.
- This is a capability result, not a protocol freeze. TRT-15 must bind the
  accepted implementations before confirmatory collection.
- Issue #353 remains open for two-agent launcher integration, complete sealed
  conformance, and protocol-freeze evidence.
