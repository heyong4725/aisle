# ADR-64 — a halt ends the episode: no in-episode resume

Status: PROPOSED (agent-drafted 2026-09-10 at the owner's direction; CON-15
interpretation recorded, proceeding). Answers open question 2 of issue #562.
Touches nothing frozen today; binds the wording of the future #562
spec-change and the H6 turn-aware swap follow-up. Human sign-off per CON-10
when #562 lands.

## Context

Issue #562 proposes a declared `halt` contract for motion nodes and leaves
open whether a halted node can be released to resume the same episode, or
whether every halt is terminal. The question is not a hardware question and
must be answered before H6 runs, because it decides what "pause a running
system and repair it" means for the operation claim.

Three facts from the record constrain the answer:

1. **The episode is the evidence unit.** CON-5 layer (d) makes outcomes
   statistical per episode; TC-7 gives every episode one goal_id and one
   result; HAR-3 retries are conditioned on task failure and counted inside
   the episode. Nothing in the evidence model has a "paused and continued"
   state.
2. **H6 does not pause anything today.** ADR-h6 amendment 4 measured that a
   HAR-10 hot-swap of a lockstep participant aborts the whole dataflow
   through the ADR-30 turn watchdog, and replaced repair with a validated
   RELAUNCH. The pending follow-up is a
   quiescence handshake between HAR-10 and the barrier. That is a
   turn-level scheduling primitive with no physical meaning; it is not a
   stop of the arm.
3. **Resuming requires re-authorizing.** SEM-6 revokes permits on goal
   change, carrier loss, track change, or authorizer restart, and forbids
   silently reusing the last good assertion. A resumed motion would need
   fresh identity evidence and a fresh permit for the carried object. That
   is what a new goal already provides.

TypeGo (arXiv 2607.05482 §5.2) distinguishes interrupt-and-return from
replace-without-return because its unit of work is a long-lived process.
AISLE's unit of work is the episode, so the distinction collapses: a halt is
always replace-without-return at the episode level, and "return" is a new
episode.

## Decision

A `halt` is terminal for the episode in which it fires.

- **No `release` topic.** The #562 contract carries `halt` and `halt_done`
  only. A halted motion node accepts no further commands for the current
  goal_id; it stays in its declared halt semantics until reset.
- **Resume is a new episode via reset.** On hardware that is the
  behavioral reset path (ADR-phase6-prep: hardware refuses teleport; A6
  priced the behavioral path at 0.80 success and +19 s per episode in sim).
  The cost is accepted: halts are authority events and must be rare. #562
  MUST add a halt-event id to the SPEC 470 exposure ledger (SFE-2 lists
  none today) so every halt is retained as a safety-exposure record.
- **The frozen judge is not told about halts.** VER-3's taxonomy is exact
  and stays exact. The halted episode's `episode_result` is whatever the
  judge says of the world (in practice `timeout`); the halt is a separate
  record keyed by goal_id in the halt ledger and the SPEC 470 exposure
  ledger. Analysis joins them and EXCLUDES halted episodes from the primary
  task contrast by a pre-registered rule, the same exclusion discipline
  SEM-8 applies to containment and guard events. This ADR adds the
  attribution rule: a halt is credited to the component that issued it,
  never to the verifier and never as a task failure of the policy.
- **The task-state-machine treats a halt as terminal, not as a HAR-3
  retry.** A retry after a halt would be motion after an authority stop
  under the same goal_id, which is exactly what the contract forbids.
- **Turn quiescence and halt are different primitives and stay
  different.** The H6 turn-aware swap follow-up MUST be built on a barrier
  quiescence handshake (ADR-30), sim-only, carrying no actuation semantics.
  It MUST NOT be implemented by issuing `halt`, and `halt` MUST NOT be
  overloaded to mean "the scheduler is between turns".

## Consequences

- #562's TC-H1 loses the `requested_semantics` resume case; question 2 is
  closed and the contract is one topic in, one receipt out.
- H6's operation claim, when run, is stated as detect, localize, and
  restore across an episode boundary, never within one. The relaunch repair
  in ADR-h6 amendment 4 is already consistent with this.
- Every hardware halt costs one behavioral reset. If a task family arrives
  whose episodes are long enough that this cost dominates (retail S2/S3
  patrol is the candidate), the trigger to revisit is a spec-change that
  adds `release` WITH a mandatory re-authorization stage under SEM-5 before
  any resumed motion. Until then, resumable halt is out of scope.
- ADR-38's chunk preemption is unaffected: it is a policy-side queueing
  rule under the guard, not a halt, and a halt during a chunk drops the
  chunk (the halt-during-chunk loopback fixture #562 proposes as HWP-25;
  that ID does not exist in SPEC 520 yet).

## What this does not decide

Questions 1 (`retract` in v1) and 3 (per-node versus graph-wide bound) of
#562 remain open for the HWP-9 safety case. Nothing here authorizes the
#562 spec-change itself; its trigger is unchanged.
