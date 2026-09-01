# Session-level statistics evidence

This directory is the synthetic, machine-readable dry run for SPEC 400 and
issue #345. It is not a confirmatory campaign and contains no physical-robot
observations. The values deliberately exercise exclusions, censoring,
clustered artifact observations, survival summaries, and a zero-event bound.

## Reproduce

From the repository root:

```bash
uv run harness stats power \
  --protocol analysis/statistics/synthetic-protocol.json \
  --output analysis/statistics/synthetic-power.json

uv run harness stats analyze \
  --protocol analysis/statistics/synthetic-protocol.json \
  --records analysis/statistics/synthetic-records.json \
  --output analysis/statistics/synthetic-analysis.json

uv run harness stats validate \
  --protocol analysis/statistics/protocol-template.json \
  --purpose freeze \
  --output analysis/statistics/protocol-freeze-refusal.json
```

All three commands write the same JSON object printed to stdout. The first two
exit zero. The last command must exit nonzero: the confirmatory template is
intentionally not frozen and has no independent review. Every generated result
records raw-input SHA-256 hashes, the analysis implementation hash, analysis
seed, method assumptions, Python version, OS release, and architecture.

## Direct inspection

The preserved synthetic run has 10 randomized assignments: 9 started, 7
completed, 8 included, 1 infrastructure-excluded, 1 right-censored, and 7
analyzed for the primary binary outcome. The typed-minus-monolithic synthetic
risk difference is about 0.417, but its 95% Newcombe interval spans roughly
-0.226 to 0.757. It therefore supports no treatment claim.

The nested held-out cell is 8/8. Its exact one-sided 95% lower bound is about
0.688, so `claim_above_threshold` is `false` for a 0.90 population-rate claim.
The zero-event result is likewise labeled synthetic: 0 events over 800
synthetic command exposures gives an exact one-sided 95% upper bound of about
0.00374, not a physical-safety claim.

`synthetic-analysis.json` is also the paper-figure input fixture. Figures must
use `binary_effect.session_points`, the arm exact intervals, the pooled
Newcombe interval, the separate `strata`, and `session_flow`; they must not
replace these session-level values with unsupported isolated bars. Nested
artifact rows live only under `artifact_outcomes` and never count as treatment
replicates.

## Confirmatory freeze checklist

1. Copy `protocol-template.json`, replace every placeholder, run `stats power`,
   and freeze the task/fault/randomization artifacts required by the campaign.
2. Run `stats validate --purpose power` and retain its
   `protocol_core_sha256` (the canonical protocol with the later `freeze`
   envelope excluded, avoiding a circular self-hash). Hash the exact analyzer,
   analysis scripts, and synthetic fixtures too.
3. Give those artifacts and `statistical-review-template.json` to a statistician
   who did not author the analyzer. The reviewer must inspect the estimand,
   session unit, assumptions/sensitivity, interval and decision methods,
   fixtures, and limitations.
4. Resolve every finding; retain the real reviewer identity/role, signature,
   external timestamp, disposition, and artifact hashes. Place that signed
   record in the protocol's `freeze.review` block with `status: "frozen"`;
   `freeze.artifact_hashes` must include `protocol_core`, `analysis_script`, and
   `fixtures` as `sha256:<digest>` values.
5. Run `harness stats validate --purpose freeze`. Do not randomize or start a
   confirmatory session unless it exits zero. Any later change uses the formal
   deviation process and a new immutable protocol identity.

No placeholder, owner self-review, synthetic signature, or admin merge counts
as the independent statistical review required by STA-12.
