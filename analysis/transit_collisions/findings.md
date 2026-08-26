# The transit-collision lever — two fixes, two re-measures, one falsified hypothesis

Date: 2026-08-26. Scope: the failure class named by both
analysis/t4_inc2/findings.md ("the same staged-transit collision class
that dominates post-breakthrough T2") and the T2 registration record.
Runs: 20260826-171645-a4b598 (T2 n=8), 20260826-175829-5b3ca5 (inc-2
recovery, post-#316). UNATTESTED dev measurements (ADR-24).

## Headline

The premise "one fix pays both tiers" is **measured false** — the two
tiers fail by different mechanisms, and the third mechanism found
behind them is different again:

| Tier | Before | After | Verdict |
|---|---|---|---|
| T2 (seeds 8..15) | 0.5; 2 collisions | 0.5; **1 collision** (seed 10 → honest `never_grasped` at budget) | collision class halved, score unchanged |
| inc-2 recovery (0,4,8) | 1/3 | 1/3, outcomes byte-identical | offset hypothesis falsified |

wrong_object stayed 0 everywhere, as always.

## Fix 1 (#314): bailed stages hand the next stage the actual position

Measured mechanism (registration seeds 10/15): a read stage bails
contact-blocked with 2.2–3.1 rad of tracking error, `current_cmd`
stays at the phantom stage target, and the next stage interpolates
from the phantom — dragging the pressed arm across the shelf. The
reseed (bail-scoped; tracked completions keep the gravity-sag command
lead) converted seed 10's collision into an honest budget exhaustion:
the box survives, the episode fails clean. Seed 15 still collides
(35.7 s vs 37.7 s), and its mechanism is now precisely named: the
read-pose DLS solve returns a WRIST-FLIPPED solution (joint 4 jumps
2.58 rad), the arm presses the shelf for the full 10 s STAGE_BAIL_S
window, and the ladder's subsequent moves sweep the contact zone.
That is solve quality in the read ladder — rejecting solutions far
from the current configuration, or bailing on tracking divergence
long before 10 s — not a transition bug.

## Fix 2 (#315/#316): the return grasp measures the box — and it didn't matter

The hypothesis (from r4's record): the delivered box lies off-centre,
so the centre-assuming return grasp strikes its edge (seed 8) or
misses it (seed 4). The fix — the return-planner drives its own
L1Session and grasps the estimate (plus #316's turn-stamp lesson:
carried request metadata must drop its ADR-30 stamp; the estimate
lands in a later turn and the wrapper stamps the active turn) — is
rung-honest and landed, and the re-measure ran all three recoveries
on the estimate path (`return plan (estimate)` × 3).

Outcomes were byte-identical to the centre baseline, and the trace
shows why: **the delivered box sits at the tray centre** ([0.348,
-0.453] vs [0.35, -0.45]) — delivery is deterministic and accurate,
so estimate ≡ centre on these seeds. The offset hypothesis was
plausible and wrong (the standing defect class: an untraced
mechanism). The estimate path remains correct engineering for boxes
that DO land offset, but it pays nothing here.

## The real seed-8 recovery mechanism (traced from oracle_state)

At the collision verdict (sim 142.63 s, 4.9 s into the recovery),
the med that moved is **amoxicillin — a shelf box at [0.387, -0.070],
40 cm from the tray** — displaced 3.3 cm while the tray box sat
untouched. The return pick fails during the DESCENT to the tray at
the workspace edge: the IK solution for the low tray reach swings an
arm link over the shelf zone and the link sweep knocks a shelf box.
An arm-configuration problem (elbow-out solve over the shelf), not a
grasp-target problem. The named fix candidates: tray-approach routing
that keeps the descent corridor clear of the shelf (side approach /
lower staging x), or configuration-constrained IK seeding for
tray-zone targets. Seed 4's `not_returned` at budget is unexamined
beyond this window and may share the mechanism (slow, obstructed
return transit).

## Ledger

- Collision mechanisms now named: (1) phantom-command drag after
  contact bail — FIXED (#314); (2) wrist-flip read solve pressing the
  shelf — open, read-ladder solve quality; (3) arm-link sweep during
  tray-zone descent — open, approach routing / IK seeding.
- The class is not one class. Score movement requires (2) for T2 and
  (3) for the recovery leg.
