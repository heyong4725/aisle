# S1 nondeterminism — the issue #71 back-to-back attested pair

**Status: CON-5 VIOLATION ESTABLISHED for the S1 (retail/mobile)
stack.** Two `expert_s1.yaml` rollouts, run back-to-back on one
machine at ONE pin with the full amended CON-5 tuple identical —
`git_sha 9019752e`, `graph_hash ca81ee88`, frozen `env_hash 025c7de2`,
`env_fingerprint 46f2ec83…` with `env_attested: true` on BOTH runs,
same platform, same seeds 100..107 — produced different results.
This removes every residual explanation issue #71 left open
(cross-pin code drift, environment drift, attestation gaps).

## The pair (records/, committed evidence)

| Seed | `s1-det-pair-1` | `s1-det-pair-2` |
|---|---|---|
| 100 | **extra_item @ 245.6 s** | **timeout @ 600 s** |
| 101 | timeout | timeout |
| 102 | timeout | timeout |
| 103 | timeout | timeout |
| 104 | **extra_item @ 229.2 s** | **timeout @ 600 s** |
| 105 | timeout | timeout |
| 106 | timeout | timeout |
| 107 | extra_item @ 294.0 s | extra_item @ 294.1 s |

pass@1 is 0/8 in both, but the failure histograms differ (3 vs 1
`extra_item`), two seeds flip failure CLASS entirely, and even the
"agreeing" seed 107 differs by 0.1 s of sim time at episode end — the
divergence is real per-episode dynamics, not scoring noise.

## What this establishes, and what it does not

- CON-5's guarantee (same tuple ⇒ same result) does NOT hold for the
  S1 sim stack. The desk tier shows no such drift (M0's T0 replicate
  was 50/50 identical; the two T1 fill-runs agreed 0.875/0.875 with
  the same failure class), so the defect is in the S1/mobile path,
  not the harness or scoring.
- Every single-session S1 pass rate in the record (A1's expert 1/8
  and 0/8 rows, H3's W 0.375 / L 0.5 cells) is a draw from a
  nondeterministic process; cross-system comparisons at n=8 carry
  this on top of binomial noise. The A1/S1 "inconclusive" verdict and
  the H3 single-session variance caveats already say this — the pair
  makes it mechanical, not hypothetical.
- The earlier INVALID-provenance rerun's 0/8 (vs the unattested 1/8)
  is now parsimoniously explained: three attested-or-not expert_s1
  sessions read 1/8, 0/8, 0/8 with shuffling failure classes.

## Candidate mechanisms (issue #71), narrowed

Unchanged shortlist, now with a concrete probe surface: seeds 100/104
flip between a ~230–245 s `extra_item` and a 600 s `timeout`, so the
divergence point is INSIDE the first ~230 s of those episodes. Next
step remains the per-node Arrow-trace bisection (`harness traces
query`) at the first divergent topic/timestep on seed 100, and the
`random`/`np.random` seed-injection audit of the mobility/base-driver
and task-planner path (CON-5: inject RNG). The full traces for both
runs are preserved in the (uncommitted) run dirs.

IDs: CON-5 (as amended by ADR-24 — the attested tuple is exactly what
diverged); issue #71; A1 §S1 caveats; ADR-h3 variance caveats.
