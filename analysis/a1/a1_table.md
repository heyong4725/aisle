# Ablation A1 — agent-composed vs expert graphs (design doc §6)

A1 asks whether agent composition carries a tax (or gain) versus the
hand-written expert. **The matched fill-runs have now landed** (both
expert cells on the held-out seeds 100..107 at one pin, run
2026-07-29/30; evidence in `records/`, runs reproducible from the
manifests there). Per-row provenance stays explicit; n=8 per cell keeps
every conclusion coarse.

## The matched comparison (held-out seeds 100..107)

| System | Tier | Commit | pass@1 | Failures | Notes |
|---|---|---|---|---|---|
| **Expert `expert_t0`** | T1 | `87b1ff66` | **0.875** | 1 `dropped` | fill-run `a1-expert-t0-T1-holdout` |
| Agent zero-shot (H1, launched graphs) | T1 | `abd2e9d3` | median 0.875 | — | 16 graphs × 8 |
| Agent EN-loop claude (H2) | T1 | `e8f163ab` | 1.0 | — | held-out |
| Agent EN-loop codex (H2, clean) | T1 | `e8f163ab` | 0.875 | 1 `dropped` | held-out |
| **Expert `expert_s1`** | S1 | `87b1ff66` | **0.125** | 5 `timeout`, **2 `extra_item`** | fill-run `a1-expert-s1-holdout` |
| Agent campaign W/S1 (H3) | S1 | `03da7469` | 0.375 | 5 `timeout` | clean cell |
| Agent campaign L/S1 (H3) | S1 | `03da7469` | 0.5 | 4 `timeout` | clean cell |

Reference rows (different tier, context only): expert_t0 @ T0 = 0.98
over 50 seeds (M0, `3644a501`, with its CON-5 determinism replicate).

## What the matched cells support

- **T1 (desk): no composition tax, modest EN-loop gain.** The expert,
  the H1 zero-shot median, and the codex EN-loop arm all sit at 0.875
  on this seed set (the codex arm's failure is even the same class,
  one `dropped`); the claude EN-loop arm's 1.0 is one episode better.
  At T1 the ceiling is high for everything and A1 discriminates
  little. Commit skew caveat: the expert cell is at the current pin,
  the agent rows at their campaign pins.
- **S1 (retail, long-horizon): agent GAIN, not tax — the expert
  baseline collapses.** Expert 0.125 vs agent-session systems 0.375
  (W) and 0.5 (L) on identical held-out seeds: the agent-iterated
  drivers are 3–4x the expert's pass rate. The expert's held-out
  failure profile (0.125, 5 timeout, 2 extra_item) **replicates its
  dev-seed profile exactly** (run `20260728-001009`, seeds 0..7:
  0.125, 5 timeout, 2 extra_item — see `analysis/h3/records/`),
  so this is the graph's real level, not seed luck.
- **The expert commits DELIVERY-class failures; the agent systems did
  not.** `extra_item` (the 10x class, RS-7) fires twice for the
  expert on held-out seeds — the L0-pick/neighbour-snag mode the H3
  S1 agents diagnosed and designed out (their held-out delivery
  count: 0). Two consequences: the H3 analysis's "delivery precision
  held" claim is a property of the *agent-built* systems, not of the
  suite; and H5's structural guarantee does not extend to
  `extra_item` (the budget guard cannot gate "picked up a neighbour
  too" — it is verifier-detected only), which the eventual H5
  writeup must state.

## Caveats

- n=8 per cell; a one-episode swing is 0.125 of pass rate.
- The expert cells ran at the current pin (`87b1ff66`), the agent
  rows at their campaign pins — the frozen env is hash-identical
  (`025c7de2`) across all of them, but graph-adjacent code drifted
  between pins.
- H3's S1 cells are single sessions (see `analysis/h3/` for the
  variance caution); the H1/H2 rows carry their own findings' caveats.
- Both fill-runs used the recorded human overrides
  (`--no-idea-gate`, `--env-baseline local`), manifests committed in
  `records/`.

IDs: design doc §6 ablation A1, RS-7 (delivery class), CON-5
(replicate accounting + provenance).
