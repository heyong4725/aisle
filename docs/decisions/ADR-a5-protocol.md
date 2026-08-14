# ADR-a5-protocol — A5 fleet scaling: 1/4/8 concurrent agents on T1

Status: ACCEPTED (owner-directed launch 2026-08-14: "run A5 fleet
scaling next", following the design doc §8.4.3 / §6 A5). Runner:
`tools/a5_fleet.py` (Class A + unit tests), reusing the ADR-h2 session
machinery per agent.

## Design

Fleet sizes N ∈ {1, 4, 8}, run SEQUENTIALLY as three configs on the one
machine (the solo config is the contention-free baseline). Per config:
N research-agent sessions run CONCURRENTLY, each in its own worktree at
one pinned OID, each under the standard research contract on tier T1
from scratch, each with its own isolated home + campaign credential and
its own dev rollouts (own sim instance — the fleet shares the HOST, not
one batched bridge; host contention is the scaling axis measured, and
the deviation from §8.4.3's shared-server design is recorded here:
shared-bridge multi-SESSION scheduling awaits the ADR-30 lockstep
runtime being wired into the rollout path).

- Budgets per agent: the desk-suite T1 split (0.4M tokens / 40 episodes
  advisory / 2.5 h wall), identical across configs — so tokens- and
  time-to-success are comparable per agent as N grows.
- Seeds: every agent gets the same dev range (0..49) — the task is
  identical T1-from-scratch; agents do not share worktrees, so there is
  no seed collision. Held-out scoring (100..107) runs SEQUENTIALLY after
  the config ends (scoring must not be contention-poisoned).
- Peer visibility (§8.4.3's cross-pollination axis) is DEFERRED in v1:
  sessions are single-turn non-interactive; "refreshed between turns"
  has no natural mapping. v1 measures the contention economics; the
  cross-pollination ablation is a separate later arm.
- Metrics per agent: tokens total and tokens-to-first-success,
  first-success wall, holdout pass@1, episodes, wrong_object (must stay
  0); per config: wall for the whole config, sim-utilization analogue
  (Σ rollout wall / session wall per agent — the MRU stand-in), and the
  ENPIRE Figure-6 comparisons (success and token totals vs N).
- Runtime identity, isolation, credential seeding, fail-closed auth
  probe, and the pinned-OID rule are inherited unchanged from ADR-h3
  (§§ as applicable); an infra abort in one agent records that agent's
  cell and does not kill the config.

## Consequences

Results under `analysis/a5/`; the config record is
`runs/a5/fleet_<N>/a5_config.json` plus per-agent scenario-style
records. The 8-lane config may thrash (16 cores, 8 sims + 8 agents) —
degradation is reported as measured, never rerun-until-pass.
