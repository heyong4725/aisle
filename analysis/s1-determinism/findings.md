# S1 nondeterminism — the issue #71 back-to-back same-recorded-tuple pair

**Status: CON-5 VIOLATION ESTABLISHED — same RECORDED tuple, different
results, observed on the S1 workload.** Two `expert_s1.yaml` rollouts,
run back-to-back on one machine at ONE pin with the recorded CON-5
tuple identical — `git_sha 9019752e`, `graph_hash ca81ee88`, frozen
`env_hash 025c7de2`, `env_fingerprint 46f2ec83…`, same platform, same
seeds 100..107 — produced different results. This removes the residual
cross-pin code doubt issue #71 left open. It does NOT fully exclude
environment drift: both runs are dev-attested only (`env_baseline:
local`, `env_baseline_oid: null`, `post_run_audit: null`) —
`env_attested: true` here records the gate-time lock check, not a
verified post-session installed-file audit (ADR-24: only trusted runs
get the full RECORD audit). A trusted-baseline rerun with a passing
post-run audit would close that residue (PR #77 review).

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
"agreeing" seed 107 differs by 0.1 s of sim time at episode end.

## What this establishes, and what it does not

- CON-5's guarantee (same recorded tuple ⇒ same result) does NOT
  hold as observed on the S1 workload. Where the defect lives is NOT
  established: the desk tier's replicates (M0's T0 50/50 identical;
  the T1 fill-runs 0.875/0.875, same failure class) are two
  observations, not a controlled isolation, and the runtime evidence
  below points at scheduling/backpressure as much as at any
  S1-specific node.
- Every single-session S1 pass rate in the record (A1's expert 1/8
  and 0/8 rows, H3's W 0.375 / L 0.5 cells) is a draw from a
  nondeterministic process; cross-system comparisons at n=8 carry
  this on top of binomial noise. The A1/S1 "inconclusive" verdict and
  the H3 single-session variance caveats already say this — the pair
  makes it mechanical, not hypothetical.
- The earlier INVALID-provenance rerun's 0/8 (vs the unattested 1/8)
  is consistent with the newly observed instability, but remains
  unattributable: that comparison still has unmatched code/environment
  provenance (PR #77 review).

## Candidate mechanisms (issue #71), reordered by the runtime evidence

**Scheduler backpressure / startup ordering is now the lead candidate
(PR #77 review).** A compact diagnostic from the preserved raw
evidence (run dirs, uncommitted):

- `dora-genesis` discarded **591,611 vs 711,863** events under queue
  backpressure (its node log's `Discarding event` count) — a ~20%
  difference in what the bridge ever saw.
- The FIRST `reset_done` differs before any seed-100 work:
  `sim_time_ns` **10,000,000 vs 0** — one run had taken a 10 ms
  physics step before its first reset completed, the other had not.
- `s1-expert`'s `joint_state` handling differs run-to-run (1,492 vs
  1,563 log mentions; the review independently counted 2,339 vs 2,447
  delivered events).

`dora_genesis` consumes its 10 ms timer and command inputs in one
event loop, so different delivery/drop ordering changes WHICH command
is applied before a given physics step — sufficient to compound into
class-flipping divergence with no RNG involved. The prior shortlist
(un-injected `random`/`np.random` in mobility/task-planner; sim-time
accumulation at the 600 s cutoff) remains open but secondary. Next
step is unchanged: per-node Arrow-trace bisection at the first
divergent topic/timestep (seeds 100/104 flip class inside the first
~230 s), now starting from the startup/reset window rather than
mid-episode.

IDs: CON-5 (as amended by ADR-24 — the recorded tuple matched and the
results diverged); issue #71; A1 §S1 caveats; ADR-h3 variance caveats.
