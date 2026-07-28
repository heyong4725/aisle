# Campaign H3 — S1 research notes (2026-07-28)

Deliverable: `graphs/agent_campaign.yaml` → `skills/s1-driver-v2/s1_driver_v2.py`
(registered skill `s1-driver-v2`). Idea tree: I1 (baseline), I3 (v2 fixes),
I5 (unseen-seed validation), all closed with observed numbers.

## Measured results (dev seeds)

| run | graph | seeds | pass1 | failures |
|---|---|---|---|---|
| 20260728-001009 | expert_s1 copy | 0..7 | 0.125 | 5 timeout, 2 extra_item |
| 20260728-031402 | s1-driver-v2 | 0,1,4,6,7 | 0.80 | 1 safe timeout |
| 20260728-041509 | s1-driver-v2 | 8..10 | 0.67 | 1 safe timeout |

v2 aggregate over dev seeds {0,1,4,6,7,8,9,10}: 0.75 pass1, ZERO
extra_item (the 10x class) vs 2/8 under the expert.

## Root causes found (from traces, not guessed)

1. **3rd counter drop always IK-fails.** `s1_expert` spreads drops in the
   BASE frame (`dy = placed*0.12`); `(0.50, 0.24)` fails
   `IK failed: transfer` deterministically, so every >=3-unit order timed
   out. Fix: re-park the base per placement along the counter
   (`PLACE_PARK_YS`), always drop at the proven base-frame spot
   `(0.50, 0.0)`. Verified up to 4-unit orders (seeds 9, 10).
2. **L0 (bottom-shelf) top-down picks jam and snag neighbours.** The
   descent crosses the L1 board; tracking bails (joint-5 err ~1.0 rad)
   and the blind close can snag a NEIGHBOUR box that then gets delivered
   -> `extra_item` fires instantly (RS-7 asymmetry). Fix: skip L0 pick
   quads at plan time; a missing item is a plain fail, 10x cheaper.
   Offline IK prototyping for a front grasp found no in-envelope chain:
   FLIP_MAX 1.2 rad vs measured 2.2+ rad wrist flips at z~0.125; the
   only solvable branch is contorted (J4 at limit) and its rise->front
   sweep grazes the shelf half-space at capture-tol corners.
3. **Deadlocks idle to timeout.** Nav failure after retries and failed
   picks used to idle forever / "place" nothing. Fix: settle-verify
   anyway after nav exhaustion; abort-on-bail releases an untrusted
   grasp at the carry tuck and skips its goto+place pair.

## Category/stock structure (why residual timeouts remain)

metformin: 0 L1 slots (ALWAYS L0-dependent); amoxicillin, cetirizine:
1 L1 slot each (qty>=2 needs L0); ibuprofen 4 L1, omeprazole 3 L1.
Under v2, an order is fulfillable iff every line fits in L1 stock —
about half of dev seeds 0..10. The single highest-value follow-up is an
L0-capable pick (front grasp with a multi-hop verified flip, or a
deeper-grip top-down with the descent kept OUTSIDE the shelf and a
horizontal entry under the L1 board — wrist-body clearance vs the board
bottom is ~1-5 cm and needs sim confirmation, not just IK).

## Budget spent (of 1M tokens / 6h wall)

Three rollouts, 16 episodes, ~5.4h wall including 2.4h burned by the
baseline's five 600s timeouts. Timeouts dominate wall cost at rtf ~0.4:
diagnose from node stderr logs (`out/*/log_s1-expert.jsonl`) before
spending episodes.
