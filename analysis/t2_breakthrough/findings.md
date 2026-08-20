# T2 breakthrough campaign: the wall broke at 0.375 (2026-08-19)

ENPIRE follow-up 5 (owner-approved): a 4-lane peer-visible Claude fleet
against T2 — the tier that held at expert 0.08 and defeated every solo
campaign arm (desk-H3 both arms 0.0, params-only, A4 both agents).
Runner: tools/a5_fleet.py --tier T2, pin fd2b763d, live peer idea-tree
symlinks (#294/#298), per-lane 400k tokens.

## Scoreboard (attempt 3; holdout seeds 100..107)

| lane | dev first success | holdout pass@1 | failure mix |
|---|---|---|---|
| 0 | 33.6 min | **0.375** | 1 never_grasped, 4 collision |
| 1 | — | 0.0 | wall_clamp ×5 (watchdog tuning backfired), 2 collision |
| 2 | infra (rc=1 at launch; isolated) | — | — |
| 3 | 41.8 min | **0.375** | 1 never_grasped, 3 collision, 1 dropped |

**wrong_object: 0 — every lane, every attempt.** Two INDEPENDENT lanes
converged on working systems scoring 4.7× the expert baseline on fresh
held-out seeds; the residual failure budget flipped from tour
starvation (the old wall) to transit collisions (a known, attackable
class).

## The two-layer story (this is the finding)

1. **The parked skills were the fuel.** Both scoring lanes started from
   `skills_pending_review/` — t2-scan-pose and t2-scan-tsm, the r2-era
   skills recovered in the #252 provenance arc, DECLINED by the ADR-37
   floor, and parked "as history." The lanes ported them to the ADR-30
   lockstep runtime (the exact turn_node/turn-outputs port this repo's
   own bring-ups documented), fixed the identity-gate starvation they
   were built for, and pushed through. The library effect desk-H3
   could not measure (its tiers were too easy or too hard) is measured
   HERE: accumulated, human-reviewed artifacts + a harder-tier retry =
   the breakthrough. Even attempt 2's burned lanes contributed — lane 0
   ported attempt-2 work product (r2-wt1's stack) across attempts via
   the durable worktrees.
2. **Ensemble + peer visibility shaped the search.** Lane summaries
   cross-reference peers' logged verdicts; lane 1's watchdog-tuning
   dead end (wall_clamp ×5) was visible to lanes 0/3, which kept stock
   watchdog values. Diverse outcomes across identical budgets (0.375 /
   0.0 / 0.375) is ENPIRE's Push-T ensemble lesson reproduced.

## Campaign infra ledger (attempts 1–2, both fenced)

- Attempt 1: codex single-use refresh rotation burned the campaign
  login and killed both codex lanes (refusal now merged, #298).
- Attempt 2: operator /login mid-campaign rotated the Keychain token;
  all four lanes died at ~28 min with 401s (credential re-exporter now
  standard launch equipment; lanes ran WELL before dying — their work
  product seeded attempt 3).
- Attempt 3, lane 2: rc=1 at launch, isolated, cause not yet traced
  (one lane of four; the campaign design absorbs it).

## What follows

- The lanes' improved skill stacks are in the r3 worktrees with
  evalcards; registration attempts go through ADR-37's floor on
  PRE-REGISTERED n≥8 suites (the t2-scan-pose lesson: n=3 flake noise
  decides nothing).
- The collision class now dominates: the staged-transit work
  (#158-era) is the named next lever, this time with 0.375 of headroom
  to spend on it.
- Budget: ~2.4M tokens total across three attempts (~1.2M on the
  scoring attempt).
