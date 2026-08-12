# ADR-30 — T4 dialogue contract (spec-change sketch, awaiting human review)

Status: PROPOSED (Class-C change to SPEC 010, CON-10/CON-14 — this PR is
the review vehicle and is NOT merged by the dev loop).

## Context

T4 (design doc §3): "Full request loop: confirm name back, deliver,
verify placement, handle 'that's the wrong one'" — probing HRI +
recovery with in-context pass@k semantics. §8.4.2 requires a scripted
human-request generator so runs are reproducible. The driver topic
contract (SPEC 010) has no dialogue topics; adding them is a Class-C
change requiring human review.

## Decision (proposed)

Two JSON topics, TC-7-correlated by `goal_id`:

| Topic | Dir | Schema | Rate | Notes |
|---|---|---|---|---|
| `human_msg` | out (human-sim) | JSON utf8 | per event | `{kind: request\|confirm_reply\|correction, med, text}` |
| `robot_msg` | out (task-state-machine) | JSON utf8 | per event | `{kind: confirm\|ack, med, text}` |

- **human-sim node** (new, curated registry slot `human-sim`): a
  SCRIPTED generator, pure function of the episode seed. Script per
  seed: (1) emit `request` naming med A = the client's target; (2) on
  the robot's `confirm` for A: reply `confirm_reply` yes; a seeded
  minority of episodes (seed % 4 == 0 proposed) instead answer with a
  `correction` naming med B (deterministic: B = next med) — the
  "that's the wrong one" path, exercised BEFORE delivery; (3) after a
  delivery of a corrected-away med, emit `correction` (recovery path).
- **task-state-machine T4 mode** (`AISLE_TASK_TIER=T4`): on goal, emit
  `confirm` naming the requested med and WAIT for `confirm_reply`
  before requesting perception (a delivery without confirmation is a
  protocol violation, counted in feedback); on `correction`, switch the
  active target to the corrected med (an in-context retry per HAR-3 —
  the retries counter increments, pass@8 semantics unchanged) and, if
  the wrong med was already delivered, first return it via the
  behavioral-reset motion path (reuse, not new machinery).
- **Verifier semantics**: judged on the FINAL corrected request — the
  goal payload gains `corrected_med` written by the CLIENT (which owns
  the script seed and can compute the final target ahead of time,
  keeping the frozen verifier ignorant of dialogue). Success = the
  final med in the tray; a delivered-then-corrected med still in the
  tray at timeout = `wrong_object` (the tier's sharpest edge).

## Consequences

- SPEC 010 §2 gains the two rows above (this PR edits the spec).
- The frozen verifier is untouched (the client computes the final
  target from the same seeded script — CON-5 keeps them in lockstep).
- pass@k semantics stay HAR-3's (corrections are in-context retries).
- The scripted generator is deliberately simple; free-text HRI is out
  of scope for the desk curriculum (the VLM-verifier tier of §7.5 is
  where open-ended dialogue would land).

## Alternatives considered

- Dialogue via `episode_goal` mutation (no new topics): rejected — the
  goal is TC-7's immutable action payload; mutating it mid-episode
  breaks every consumer's idempotency assumptions.
- Free-text messages with an LLM judge: rejected for the desk tier —
  reproducibility (CON-5) is the experiment's spine.
