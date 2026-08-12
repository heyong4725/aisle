# ADR-32 — T4 dialogue contract (spec-change sketch, awaiting human review)

Status: PROPOSED (Class-C change to SPEC 010, CON-10/CON-14 — this PR is the
review vehicle and is NOT merged by the dev loop). Originally drafted as a
second "ADR-30" before #171 ratified that number for the lockstep-turn
protocol; renumbered to ADR-32 (ADR-31 is the T2 frozen-scene sign-off) and
amended 2026-08-11 after the PR #170 review found four blocking holes.
Relates to ADR-30 (lockstep turns), SPEC 010 TC-2/TC-7/TC-8, SPEC 040
VER-1/VER-3/RST-2, SPEC 050 CAP-1/CAP-5, SPEC 070 HAR-1/HAR-3, CON-4, CON-5.

## Context

T4 (design doc §3): "Full request loop: confirm name back, deliver, verify
placement, handle 'that's the wrong one'" — probing HRI + recovery with
in-context pass@k semantics. §8.4.2 requires a scripted human-request
generator so runs are reproducible. SPEC 010 has no dialogue topics; adding
them is Class C.

The review (PR #170) showed the first draft could not execute its own tier:
VER-3 fires `wrong_object` the moment any non-target box ENTERS the tray, so
"delivered-then-corrected med still in the tray at timeout" never happens —
the post-delivery recovery path was dead on arrival under the frozen verifier
the draft claimed not to touch. The draft also handed the corrected answer to
the policy side (the task-state-machine consumes `episode_goal` in every
expert graph), left TC-7's goal schema unamended (deadlocking corrected
episodes), and predated the ADR-30 lockstep rules its message cycle violates.
This amendment restructures the contract around those four constraints and
splits the tier into two increments so nothing ratified here is unexecutable.

## Decision (proposed)

Two JSON topics, TC-7-correlated by `goal_id`, carrying TC-2 metadata (and,
in a lockstep graph, TC-2's turn metadata):

| Topic | Dir | Schema | Rate | Notes |
|---|---|---|---|---|
| `human_msg` | out (human-sim) | JSON utf8 | per event | `{kind: request\|confirm_reply\|correction, med, text}` — FORWARD edge |
| `robot_msg` | out (task-state-machine) | JSON utf8 | per event | `{kind: confirm\|ack, med, text}` — `turn_edge: episodic` |

### 1. The policy learns the task from the dialogue, not the goal

In T4 mode (`AISLE_TASK_TIER=T4`) the task-state-machine does NOT consume
`episode_goal`. Its task arrives as the human-sim's `request` naming med A —
that is the tier: the robot must listen. The TC-7 goal payload is unchanged
(`{tier, target_med, timeout_s, seed}`); the client — which owns the seed
script and can run it ahead of time — sets `target_med` to the FINAL
corrected target (B on corrected seeds, else A). The goal is consumed only by
`verifier-*` nodes and measurement taps.

This resolves both review blockers at once: the frozen verifier judges the
final target through its existing schema (no `corrected_med` field, no
frozen-set change), and the policy side never sees the answer — the state
machine's `confirm` names A because A is what the human asked for, which is
also exactly what the human-sim script keys on (no deadlock). The
implementing PR MUST add a VAL-6-style validator rule (working name
`DIALOGUE_GOAL_LEAK`) rejecting any T4 graph that routes `episode_goal` to a
node that is neither `verifier-*` nor a measurement tap, and MUST route
`human_msg` to the state machine in every T4 graph — without the rule the
blinding is advisory, the same failure VAL-6 exists to prevent for
`oracle_state`.

### 2. Increment one: confirm + pre-delivery correction (frozen verifier as-is)

- **human-sim node** (new; added to the curated core): a SCRIPTED generator,
  driven by turn/episodic handlers in lockstep graphs. Script per goal:
  (1) emit `request` naming med A; (2) on the robot's `confirm` for A, reply
  `confirm_reply` yes — except on corrected seeds, where the reply is a
  `correction` naming med B. Correction predicate: TC-7's per-goal `seed`,
  `seed % 4 == 0`. B = the med after A in SCN-2's fixed name list
  (amoxicillin, ibuprofen, cetirizine, omeprazole, metformin), cyclic, so
  B != A always. Because HAR-1 seed ranges are contiguous, the realized
  correction fraction depends on the range's offset mod 4; the exact
  corrected set is a seed-derived artifact and is attested under CON-5(a).
  A's identity per seed is likewise seed-derived (the same sampling the
  client uses for `target_med` today), so human-sim needs no goal edge — its
  inputs are `robot_msg` plus the per-goal seed delivered in `human_msg`'s
  own scripting context (the node receives `{goal_id, seed}` at episode
  start via its manifest-declared `episode_meta` input from the client, a
  FORWARD edge carrying no target information beyond what the script
  derives).
- **task-state-machine T4 mode**: on `request`, emit `confirm` naming the
  requested med and WAIT for `confirm_reply`/`correction` before requesting
  perception (a delivery without a completed confirm exchange is a protocol
  violation, counted in `episode_feedback`); on a pre-delivery `correction`,
  switch the active target to B and re-confirm once.
- **Counting**: dialogue corrections increment a NEW `dialogue_corrections`
  counter in `episode_feedback`. They are NOT HAR-3 retries — HAR-3 retries
  fire on subtask FAILURE, and a scripted correction is not a failure.
  `retries` keeps its meaning; pass@1/pass@8 stay comparable across tiers.
- **Determinism**: the script is a pure function of `(goal seed, observed
  robot_msg sequence)`. CON-5(a) binds the seed-derived artifacts (A, the
  corrected set, B); the realized message TRACE additionally depends on
  robot behavior and is bound by CON-5's trajectory layers, exactly like
  every other closed-loop topic. In a lockstep graph both nodes are turn
  participants (each has a forward path to bridge commands), `robot_msg` is
  the episodic back-edge that keeps the dialogue cycle legal under VAL-2
  CLOCK_CYCLE, and neither node emits turn-stamped messages from wall
  handlers (ADR-30 §1.3/§1.4).

Increment one is fully executable under the frozen verifier: on corrected
seeds the correction lands at the confirm phase, before any motion, so med A
never enters the tray and VER-3 never fires early.

### 3. Increment two: post-delivery recovery (gated on a VER amendment)

"Handle 'that's the wrong one'" AFTER delivery cannot be scored by the
current frozen verifier: VER-3's immediate `wrong_object` kills the episode
when A is delivered against final target B, and success today does not
require the tray to be free of non-target boxes at episode end (an
unreturned A would score clean). Increment two therefore ships as its own
env-change epoch (CON-7 human sign-off) that:

- structures the recovery as a SECOND TC-7 goal (return A, deliver B) so
  each goal's target is fixed and per-goal judging stays frozen-simple;
- amends VER-1/VER-3 (frozen set) so a goal may carry `expects_return`
  semantics: pre-existing tray content at goal start does not trigger
  `wrong_object`, and success additionally requires no non-target box
  REMAINING in the tray at goal end;
- adds the delivery-observation edge human-sim needs for its step-(3)
  script (`episode_result` of goal one as an EPISODIC input — the human
  knows what they received; this is ground truth the human legitimately
  possesses, not a policy leak, and it is not self-attested `ack`);
- names the return motion honestly: a NEW `return_item` state-machine
  behavior reusing the grasp/IK skill stack. It is NOT RST-2 — the TC-6
  reset service takes `(seed, mode)` with no object parameter, returns the
  TARGET to a SAMPLED pose, blacks out observations while it runs, and
  falls back to TELEPORT, all of which are wrong mid-episode.

Ratifying this ADR ratifies the increment-two SHAPE; its VER wording lands
with that epoch's own review.

## Consequences

- SPEC 010 §2 gains the two rows above (this PR edits the spec;
  declarative pre-implementation per the SPEC 040 preamble convention —
  the implementing PR upgrades to RFC-2119 MUST with citing tests, HAR-9).
- TC-7's goal schema is UNCHANGED; T4 redefines who may consume it
  (validator-enforced, §1).
- The frozen verifier is untouched in increment one; increment two's VER
  amendment is declared here and reviewed with its own env-change.
- The implementing PR adds `human-sim` to `registry/schema/curated_core.toml`
  (CAP-5 Class C rides the same spec-change train) with a CAP-1 manifest
  declaring `robot_msg` as `turn_edge: episodic`.
- CON-4 classifies JSON as legal only on `*_result`/report topics; the
  dialogue topics are per-event report-class traffic, and the implementing
  PR amends CON-4's parenthetical to name `*_msg` dialogue topics
  explicitly rather than extending the exception silently.
- pass@k semantics stay HAR-3's; `dialogue_corrections` is a new counter,
  not a retry.

## Alternatives considered

- Dialogue via `episode_goal` mutation (no new topics): rejected — the goal
  is TC-7's immutable action payload; mutating it mid-episode breaks every
  consumer's idempotency assumptions.
- `corrected_med` as a goal field for the verifier: rejected by review —
  either it changes the frozen verifier's read schema or it deadlocks the
  confirm handshake; and any goal field is visible to every goal consumer,
  un-blinding the policy (PR #170 finding 2).
- Free-text messages with an LLM judge: rejected for the desk tier —
  reproducibility (CON-5) is the experiment's spine.
- Scoring post-delivery recovery under the unamended verifier: rejected —
  structurally impossible (VER-3 immediate trigger); hence the increment
  split.
