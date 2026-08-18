# A5 fleet-scaling findings (1/4/8 agents on T1, pin 3819484e, 2026-08-14)

ADR-a5-protocol (owner-directed): fleet sizes sequential, N concurrent
isolated research-agent T1 sessions per config on one 16-core/128 GB
host, per-agent budgets identical (0.4M tokens / 2.5 h), holdout
scoring sequential after each config. Campaign record:
`runs/a5/a5_results.json`. Deviation from §8.4.3 recorded in the ADR:
the fleet shares the HOST (own sim per agent), not one batched bridge;
peer cross-pollination deferred.

## Headline table

| fleet N | config wall | first-success wall (med [min–max]) | tokens/agent (mean) | tokens total | holdout pass@1 | successes/hour |
|---|---|---|---|---|---|---|
| 1 | 37.4 min | 10.5 min | 191k | 191k | 1.0 | 1.6 |
| 4 | 58.7 min | 14.1 min [11.1–17.4] | 233k | 933k | 1.0 ×4 | 4.1 |
| 8 | 97.3 min | 18.0 min [5.7–21.3] | 252k | 2.01M | 1.0 ×8 (one lane dev-null*) | 4.3 |

*fleet-8 agent 4 never logged a dev-seed success in-session yet its
deliverable scored 1.0 on holdout — the deliverable-quality-vs-dev-luck
split seen before in desk-H3 L/T2.

wrong_object: **0 across all 13 lanes** (H5 under 8-way concurrent
agent-authored iteration).

## The scaling story (ENPIRE Figure-6 comparison axes)

1. **Success throughput scales sub-linearly and saturates**: 1.6 → 4.1
   → 4.3 successes/hour. Going 4→8 agents bought almost nothing
   (+5% throughput for 2× agents and 2.2× token burn) — on one host,
   ~4 concurrent sim+agent lanes is this machine's knee.
2. **Per-agent latency degrades gracefully**: median first-success 10.5
   → 14.1 → 18.0 min (1.34× at N=4, 1.71× at N=8) — contention slows
   every lane but breaks none (all 13 deliverables scored 1.0).
3. **Token super-linearity is real but modest** (ENPIRE's fleet
   finding, reproduced at laptop scale): mean per-agent spend rises
   +22% (N=4) and +31% (N=8) over solo — slower sims mean more
   waiting-shaped agent turns per success.
4. **Quality is contention-invariant on T1**: holdout 1.0 everywhere —
   the degradation axis is time/tokens, never the delivered graph.

## Protocol machinery notes (fixed en route)

- run_session's ceilings contract requires `prior_wall_s`; the missing
  key infra-errored every lane in seconds (fix + contract-pinning test,
  PR #218). Attempt 1/2 records preserved under
  `runs/a5/attempt1-infra-error/`.
- Quarantining a failed attempt's config dirs orphans git worktree
  registrations — `git worktree prune` before relaunch.
- The credential re-exporter (Keychain → campaign file on rotation) ran
  for the whole campaign: zero 401s across 13 sessions.

## MRU decomposition (retroactive, ENPIRE follow-up 1 — 2026-08-18)

`tools/mru_report.py` over the recorded lanes (rollout wall reconstructed
from run-dir file mtimes clipped to session windows; full data in
`mru_report.json`):

| fleet N | MRU-analogue (mean sim utilization) | per-lane spread | MTU (mean tokens) |
|---|---|---|---|
| 1 | 0.862 | — | 191k |
| 4 | 0.862 | 0.845–0.878 | 233k |
| 8 | 0.858 | 0.771–0.915 | 252k |

**The contrast with ENPIRE:** their MRU *declines* with fleet size
(robots idle while agents read logs and wait on the model). Ours is
FLAT at ~0.86 — every lane keeps its sim occupied at every scale, and
the degradation we measured (median first-success 10.5→18.0 min) shows
up as slower sim seconds, not idle sim seconds. The bottleneck is in a
different place: theirs was agent think-time against real robots that
wait; ours is host physics throughput that stretches under contention.

**Honest caveat on the ratio:** a contended sim runs slower, and wall
spent inside a slow rollout counts as "utilized" — so flat utilization
under load partly reflects stretched sim wall, not constant useful
work per second. The per-lane spread at N=8 (0.77–0.92) is the
contention signature. Distinguishing busy-and-fast from busy-and-slow
needs sim-time-per-wall-second inside rollouts — available in
manifests going forward (`durations` now recorded), so the next fleet
campaign gets the clean version of this metric for free.
