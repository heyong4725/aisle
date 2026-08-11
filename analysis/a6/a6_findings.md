# A6 — teleport vs behavioral reset (design doc §6)

Idea I16 (closed `flat`, 3/4 pre-registered expectations met — the miss
is the measurement's point). Epoch `caf70e5e9` (post-#165). Paired
10-episode T1 arms, seeds 0..9, oracle verifier, idle machine.

| arm | graph | pass@1 | failures | wall | reset outcomes |
|---|---|---|---|---|---|
| teleport | expert_t1.yaml | 1.00 (10/10) | — | 6.4 min | 10 teleport |
| behavioral | expert_t1_behavioral.yaml | 0.80 (8/10) | 2 never_grasped (seeds 5, 9) | 9.6 min | 7 behavioral success / 3 fallback (`fallback: true` audited) |

Overhead: +19 s per episode. wrong_object: 0 in both arms.

## What teleporting hides (the ablation's question)

Two distinct things:

1. **The reset is itself a manipulation task** (ENPIRE's claim,
   confirmed): 30% of behavioral attempts could not return the box
   (episode 0 has no delivered box by construction; two more slipped or
   failed verification) and needed the teleport fallback. The fallback
   discipline (retry ≤3 → teleport with `fallback: true`, RST-2) kept
   every failure honest and un-hung.
2. **Behavioral resets accumulate scene drift.** Only the delivered box
   returns — to a *sampled* slot, while the other boxes stay wherever
   the previous episode left them. Episodes then run on progressively
   non-canonical layouts, and 2/10 failed honestly (`never_grasped`)
   on drifted geometry the seeded curves never see. Teleport hides this
   entirely: same-seed teleport episodes always start from the canonical
   layout.

Implication for real-hardware parity: curve numbers measured under
teleport resets are an upper bound; a physical desk pays both the reset
time and the drift tax.

## Provenance

- Runs: `a6_teleport` / `a6_behavioral` result JSONs (20260811, trusted
  `--env-baseline origin/main`, budget-ledger settled).
- Reset outcome trail: `log_reset.jsonl` in the behavioral run's out/
  dir — 7× "success in N attempt(s)", 3× fallback.
- RST-2 stack: PRs #162 (plumbing), #164 (motion), #165 (wiring).
