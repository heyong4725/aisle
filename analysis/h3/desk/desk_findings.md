# Desk-suite H3 campaign findings (T1→T4, pin dd4e3f1a, 2026-08-12..14)

Two arms over the §8.4.2 ASPIRE ladder via `tools/h3_campaign.py --suite
desk` (ADR-h3 desk amendment): arm W wiped to the pin between scenarios,
arm L keeping its registered skills + read-only idea tree. All sessions
isolated (issue #96 rules), dedicated campaign login, runtime identity
1fedbc1f… (pin-era dora pair) bracketed throughout.

## Verdict

**UNDECIDED under the strict admissibility rules** — `met: null`, no
clean cell pair on any post-T1 tier (13 caveats; every flag derived from
records, none hand-annotated). The interpretable direction, stated with
that caveat: **no ASPIRE speedup was measured on this suite.** On the
only tier where both arms produced clean-ish first-success numbers (T4),
the ratio is ~1.03 (L 894 s vs W 872 s — parity, not ≤0.5); on T2/T3 the
library did not rescue what wiped sessions couldn't do either.

## Cells (from analysis/h3/desk/desk_cells.md)

| arm/tier | attempt | first success | holdout pass@1 | flags |
|---|---|---|---|---|
| W/T1 | 1 | 8.8 min | 1.0 | wipe_leak* |
| W/T2 | 1 | 34.8 min (dev) | 0.0 | wipe_leak*, treatment_drift† |
| W/T3 | 1 | — (token budget) | null‡ | wipe_leak*, holdout_partial |
| W/T4 | 2 (401 rerun) | 14.5 min | 1.0 | wipe_leak* |
| L/T1 | 1 | 8.3 min | 1.0 | clean |
| L/T2 | 1 (cross-pin) | — | 0.5 | frozen_drift, treatment_drift — EXCLUDED (ran at a79e3d33) |
| L/T2 | 2 (at pin) | — (token budget) | 0.0 | holdout_partial |
| L/T3 | 1 (cross-pin) | — | null | EXCLUDED (a79e3d33) |
| L/T3 | 2 (at pin) | — (token budget) | null‡ | holdout_partial |
| L/T4 | 1 (cross-pin) | — | 1.0 | EXCLUDED (a79e3d33) |
| L/T4 | 2 (at pin) | 14.9 min | 1.0 | clean |

**wrong_object: 0 across every cell of both arms** (H5 holds under
agent-authored code at every tier).

## What the flags mean (and what needs follow-up)

- *`wipe_leak` on all W cells is a flag-semantics artifact, not a leak:
  `s1-driver-v2`/`s3-driver-v1` were registered in the RETAIL campaign
  and human-merged to main, so they are TRACKED at this pin — the wipe
  (defined as "detach byte-exact at the pin") restores them by
  construction, and both arms started with them symmetrically. The
  analyzer's rule ("non-empty prior skills on the wiped arm") predates
  pin-tracked skills. Follow-up: derive wipe_leak only from prior skills
  NOT tracked at the pin. Note the direction: clearing this flag would
  make T4 decidable at ratio ~1.03 → per-tier False → verdict NOT MET;
  we report the strict undecided verdict and this analysis separately
  rather than adjusting rules after seeing results.
- †`treatment_drift` on W/T2 is unexplained: its recorded rollout shas
  all equal the pin. Needs a derivation trace before any rule change.
- ‡`holdout_partial` on the T3-class cells is the scoring path FAILING
  (`{ok:false, error:null}` from the holdout rollout), not a scored
  zero — likely deliverable-vs-tier interaction; needs a rescore with
  diagnosis before those cells can decide anything.

## The scientific reading (caveated)

1. **T1/T4 are solvable from scratch** by a fresh session inside one
   sub-budget (both arms 1.0, ~9/15 min to first success). The T4
   dialogue loop was not the barrier the tier table anticipated — the
   ADR-32 contract is learnable from the research contract alone.
2. **T2 is the wall** (0.0/0.0/0.5† across four cells, †inadmissible),
   and **T3 is beyond both arms** at these budgets — the deliberate
   rearrangement gap held. The desk transfer curve therefore never got
   the chance to show a library effect: the tiers are either too easy
   (no headroom for speedup) or too hard (no success to speed up) at
   these sub-budgets. That is itself the finding: **the ladder's
   difficulty spacing, not the library, is what this campaign measured.**
3. Real skill reuse DID occur (L/T3-r2's deliverable embeds
   `s3-driver-v1`; L/T2-r2 registered `t2-scan-pose`/`t2-scan-tsm` which
   rode into T3/T4) — the mechanism works; the outcome delta at these
   budgets was zero.
4. Protocol machinery findings, each already fixed and merged or
   PR'd: guard crash on pin-tracked skills (#191), cross-pin resume
   refusal + chained-rerun allowlist (#202), the 401 token-lifetime
   class (operational: re-export per launch), holdout-scoring windows
   for T2-class tiers.

## Provenance

- Campaign dir: `runs/h3_desk` (aggregates h3_results-prev1..3, -r2,
  final; excluded_cross_pin/ holds the a79e3d33 invocation + README).
- Analysis: `desk_analysis.json` + `desk_cells.md` (tools/h3_analysis.py
  --suite from treatment), this file.
- Budget spent (ledger): ~470 episodes-equivalent of session work,
  ~4.3M tokens across 11 sessions; ceilings never breached.
