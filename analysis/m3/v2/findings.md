# M3 v2 findings — the spread population, and three measured fidelity boundaries

Protocol: ADR-m3-protocol amendment 1 (population + predictions
pre-registered; the staging note recorded mid-campaign before the
affected side re-ran). Records: records.json (Genesis + surrogate per
graph); analyzer `tools/m3_spread.py --analyze`, join-by-id (a
positional-join defect was caught when it printed rows the records
contradicted — fixed and noted in the code). UNATTESTED free-run dev
measurement.

## The ground truth worked exactly as designed

Genesis over the 16-graph population, fresh, 8 seeds each:
1.0 (clean, fast/normal velocities, wiring variant, all six authored
graphs) → 0.375 (two slow-velocity variants, timeout-graded) → 0.0
(all three measured faults, each failing by its own mechanism:
never_grasped+dropped / never_grasped / timeout). The population has
real, mechanism-diverse variance — the instrument gap v0 named is
closed on the ground-truth side.

## Headline numbers

- **Spearman (all 16): 0.746**; excluding the pre-registered
  contact-geometry set: 0.759; screening agreement 0.562. Far from
  the 0.995 literature reference, and the reasons are now itemized
  boundaries rather than a mystery.
- L0 fidelity is high and rank-preserving: every authored L0 graph
  scored 0.875 in the surrogate vs 1.0 in Genesis (the seed-3
  systematic miss, constant as in v0).

## Three fidelity boundaries, measured in one campaign

1. **Turn protocol.** The constructed variants derive from lockstep
   expert_t1; the free-run surrogate starves the barrier (0 episodes,
   10/10). Fixed for this campaign by stripping turn machinery in
   surrogate staging (lockstep is opt-in by env; recorded in the ADR
   before the re-run).
2. **Perception rung.** The v0 surrogate stubbed L1 sensors; the
   whole expert_t1 family then fails `never_grasped` with the
   estimator silent (7/7 on clean). FIXED STRUCTURALLY: the surrogate
   now rasterizes genuine seg/depth at the overhead camera through
   the verifier's own projection (VER-8 conventions, SCN-5 nominal
   calibration, exact top-face quads) — pinned by a round-trip test
   in which the FROZEN L1 estimator recovers the cartoon box within
   5 mm.
3. **Free-run event race.** With genuine L1 sensors the estimator
   still delivered only ~2 target poses across 8 episodes (258
   dropped joint_state events in the same run): the free-run firehose
   starves the once-per-request estimate path that lockstep exists to
   discipline. The clean variant scores 0.125 in the surrogate vs 1.0
   in Genesis for THIS reason, not geometry. The structural fix is
   the v0 follow-up already on record — surrogate lockstep
   participation — and this campaign upgrades it from "recorded
   follow-up" to "measured prerequisite for any L1 screening claim".

## Predictions vs outcomes (pre-registered)

- P1 (timing variants rank): PARTIAL — fault_f3 (executor stall)
  transfers exactly (0.0/0.0); the velocity gradient is masked by
  boundary 3 (all L1 rows race-dominated). Not decidable this pass.
- P2 (contact-geometry faults swallowed by the cartoon): NOT
  CONFIRMED as stated — fault_f1/f2 scored 0.0 in the surrogate, but
  via boundary 3 rather than via attach-rule physics, so the
  prediction's mechanism went untested. Honest verdict: unresolved.
- P3 (mid-range Spearman, rising on exclusion): CONFIRMED in shape
  (0.746 → 0.759), for partly different reasons than predicted.

## What v2 leaves in place

The env-ladder swap remains mechanically excellent (every graph ran
unmodified); L0 screening is genuinely usable today (rank-preserving
at ~100x speed); L1 screening awaits exactly one structural feature,
now measured rather than assumed. The GPU-class learned backbone
inherits the same requirement.
