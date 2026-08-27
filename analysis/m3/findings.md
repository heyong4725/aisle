# M3 findings — neural-env ranking agreement, v0 surrogate (2026-08-27)

Protocol: ADR-m3-protocol (pre-registered). Population: the 16
launchable H1 first-graphs (committed under population/ with sha256 +
recorded Genesis pass@1). Backend: world-model-env v0 (deterministic
kinematic surrogate). Records: records.json; every number recomputed
by `tools/m3_ranking.py --analyze`. UNATTESTED free-run dev
measurement, per the ADR.

## Verdict: UNDECIDABLE on this population — and the reasons are the finding

**Spearman rho: None (undefined).** The surrogate assigns every graph
an identical 0.875 (zero variance — the pre-registered tie rule
reports None rather than a fabricated coefficient). Screening
agreement 0.812 is a tie artifact and carries no signal.

Three stacked causes, each independently informative:

1. **The population has almost no Genesis variance to preserve.**
   13/16 graphs score exactly 0.875; the outliers are 0.75 (2) and
   1.0 (1). The launchable H1 graphs are minor variants of the same
   registry composition — the graphs that differ MEANINGFULLY are the
   24 that never launched, and their differences (dependency choices)
   are invisible to any environment. The H3 lesson recurs at the
   environment tier: **a ranking benchmark is only measurable on a
   population with outcome spread, and the spread must be located
   before the campaign.**
2. **The v0 surrogate compresses the remaining variance to zero.**
   The 0.75/0.875/1.0 differences in Genesis are contact-graded
   (collision/dropped episodes); cartoon physics has no contact, so
   they vanish — exactly the fidelity gap the ADR pre-declared. This
   sizes what a learned backbone must add: contact-outcome
   discrimination, nothing less.
3. **The surrogate has one systematic miss of its own**: seed 3 fails
   `dropped` in every graph (a layout the cartoon attach/settle rule
   mishandles) — a constant offset, harmless to ranking, named here
   so nobody mistakes it for a graph property.

## What DID work: the environment swap itself

All 16 graphs ran UNMODIFIED against the swapped environment node
(`dora-genesis` -> `world-model-env`, same wiring, full pipeline:
planner, IK, guard, frozen verifier) — 128 episodes, zero launch
failures, ~100x faster than Genesis episodes. The §7.5 ladder's
mechanical claim (environment tiers are a node swap) is exercised and
holds. The screening SPEED exists; the screening SIGNAL awaits a
population with spread and a backend with contact.

## Recorded follow-ups

- A spread population: parameter-perturbed expert variants (grasp
  offsets, approach heights, velocity scales) with fresh Genesis
  ground truth — the controlled way to put outcome variance in the
  instrument's measurable band.
- The learned backbone (Cosmos/DreamDojo-class) behind the same node
  surface — GPU-gated (owner decision).
- Lockstep participation for attested surrogate runs.
