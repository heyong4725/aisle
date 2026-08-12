# T3 expert baseline (design doc §3 — the deliberate hole)

Idea I17 (closed `up`, all pre-registered expectations met).

| tier | graph | pass@1 | failures | wrong_object |
|---|---|---|---|---|
| T3 (occluded target) | expert_t3.yaml | **0.00** (0/5, seeds 0–4) | 5× never_grasped (honest timeouts) | 0 |

The occlusion layout parks the seed-designated target (`occluded_target`
rule: med `seed % n`, the same med the T3 rollout client requests) in
level 0's covered band — under the upper board, front-grasp-only — with
a blocker box 1.5 cm in front of its face. The expert graph fails
honestly everywhere: L1 perception cannot see the target from overhead,
and the planner refuses the blocked front approach. **The
occlusion-rearrangement skill is deliberately not shipped** (§3:
"deliberate holes for the agent to fill — T3 forces authorship"); this
floor is what campaign agents must beat.

## Defects the baseline run itself caught (both fixed)

1. **Attestation vs reality**: the first baseline scored pass@1 1.0 —
   occlusion was applied at scene BUILD, but episode scenes come from
   the reset teleport path, so a T3-attested graph ran plain-T1
   episodes. The reset path now applies the same post-pass. The
   giveaway was a number too good against the pre-registration.
2. **Scene self-consistency**: the second baseline's seed-0 episode
   closed `dropped` at t=0.36 s with no robot contact — the level-1
   board is too shallow for a two-box pair and the target teetered off
   its rear edge. The pair now always occupies level 0 (settle audit:
   0.0 cm drift over 5 s across seeds 0–5).
