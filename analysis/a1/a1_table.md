# Ablation A1 — agent-composed vs expert graphs (design doc §6): data inventory

A1 asks whether agent composition carries a tax (or gain) versus the
hand-written expert. **No matched comparison exists yet** — this
document inventories what the records actually support, with tier,
seeds, and revision stated per row, and names the runs that would make
the cells comparable. Conclusions are deliberately withheld until
matched cells exist (PR #52 review).

## What the records support (per-row provenance)

| System | Tier | Seeds | Commit | pass@1 | N | Notes |
|---|---|---|---|---|---|---|
| Expert `expert_t0.yaml` | T0 | 0..49 | `3644a501` | **0.98** | 50 | M0 gate; the second 50-episode run is the SAME seeds at the SAME commit — the CON-5 determinism replicate (identical 49/50), NOT additional independent episodes |
| Agent zero-shot (oracle-pose family) | T1 | 100..107 | `abd2e9d3` | 0.75–1.0, median 0.875 | 16 graphs × 8 | H1, `analysis/h1/` |
| Agent EN-loop (claude) | T1 | 100..107 held-out | `e8f163ab` | 1.0 | 8 | H2 |
| Agent EN-loop (codex, clean) | T1 | 100..107 held-out | `e8f163ab` | 0.875 (dev 0.967/30) | 8 | H2 |
| Agent (H3, arms W/L) | S1–S3 | 100..107 held-out | `03da7469` | pending | — | campaign in flight |

## What the records do NOT support

- **Expert-vs-agent on the same tier**: the expert row is T0 (fixed
  pose); every agent row is T1 (named among 5, randomized poses) —
  different seeds and revisions besides. No tax/gain conclusion is
  drawable from these rows.
- **An expert S1 baseline**: the 13 `s1-gate-*` runs are ALL seed 1,
  single-episode, across six development commits (`c51b8a99` …
  `8bd6b234`) — a development trajectory of one repeated seed, not a
  baseline. Their 8/13 aggregate is recorded here only to prevent its
  reuse as one.

## Runs required to fill A1 (queued for post-campaign machine time)

1. `expert_t0.yaml` under `--tier T1`, seeds 100..107, 8 episodes,
   current pin — the matched expert cell for every T1 agent row.
2. `expert_s1.yaml`, `--tier S1 --embodiment mobile`, seeds 100..107,
   8 episodes, current pin — the matched expert cell for the H3 rows.
3. (After H3 lands) the agent S1–S3 held-out rows from
   `runs/h3/h3_results.json`.

With cells 1–3 filled at one pin and one seed range, this becomes a
real comparison; until then it is an inventory.

IDs: design doc §6 ablation A1; CON-5 (replicate accounting).
