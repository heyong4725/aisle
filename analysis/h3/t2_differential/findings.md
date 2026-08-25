# The T2 accumulation differential: equal score, 35% cheaper (2026-08-25)

Step 4 of the owner's 1→2→4 plan — the contrast the breakthrough and
registration made possible: one pin (bd2799ad), one tier (T2), arm W
wiped to the curated core (the #305 wipe verifiably removed the
pin-tracked t2 stack — 19 paths), arm L keeping the registered library.
Identical budgets (0.8M/6h), seeds, contract, scorer.

| arm | holdout pass@1 | session tokens | stopped | reuse in deliverable |
|---|---|---|---|---|
| W (curated core) | 0.25 | 696k (87%) | agent_done | — |
| L (registered stack) | 0.25 | **451k (56%)** | agent_done | **t2-scan-pose, t2-scan-tsm** |

**The honest verdict: the library equalized COST, not CEILING.** Both
arms reached 0.25 on fresh holdout seeds (3× the 0.08 stock expert);
the library arm got there with 35% fewer tokens, stopped by its own
choice, and its deliverable verifiably embeds the registered skills —
the reuse mechanism working exactly as designed. The wiped arm
re-derived comparable competence from scratch at materially higher
cost. ADR-h3's ≥2× time-to-first-success criterion remains formally
undecidable (neither arm logged a dev-seed success in-session; both
deliverables scored only at holdout) — the criterion measures dev-seed
speed, and T2's dev difficulty leaves it blind to exactly the effect
measured here.

Convergent with A3's schema-as-subsidy result: on this substrate,
accumulated/typed capability shows up first as ECONOMY (tokens, wall,
search pruned) rather than raw score. Score parity at 0.25 also says
the registered stack is not yet a ceiling-raiser without iteration —
consistent with its 0.5-at-the-floor registration. The named lever
stands: the transit-collision class dominates both arms' failures
(5/12 combined) and both tiers (T2 + inc-2 recovery).

Campaign infra ledger: two /login-rotation 401 kills (the class is now
familiar; the credential re-exporter mitigates future fleets), and one
machinery finding — infra-aborted attempts write no scenario record, so
`--attempt N+1` correctly refuses and the recovery is a plain relaunch
(slot rotation, PR #61 machinery). wrong_object 0, both arms, all attempts.
