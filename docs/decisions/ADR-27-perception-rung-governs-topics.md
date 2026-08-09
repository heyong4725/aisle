# ADR-27 — The perception rung governs TOPICS a policy consumes, not information in the scene

Status: ACCEPTED (owner decision 2026-08-08, CON-15). Renumbered from 26 to 27
in the same review round that created it: 26 was already taken by the ratified
CON-5 layered-reproducibility ADR that specs/000-constitution.md cites, so the
original number pointed readers at an unrelated document. Relates to SPEC 010 TC-9,
SPEC 060 VAL-6/VAL-8, SPEC 040 VER-1..3.

## Problem

VAL-8 forbids the bridge's `poses` topic at rung L1. Review of PR #125 raised a
fair objection: `poses` is not the only channel carrying ground-truth pose.

- SPEC 010 gives `poses` "identical layout and ordering contract to
  `oracle_state`" — the two are the SAME `posearray7d_f32` array, published on
  two topic names so VAL-6 can keep `oracle_state` verifier-only.
- VAL-6 lets any `verifier-*` node consume `oracle_state`. Measured on this
  branch: an L1 graph wiring `dora-genesis/oracle_state -> verifier-oracle`
  validates `ok=true`, zero errors.
- `base_pose` (`base_pose3d_f32`) is ground-truth base localisation that every
  mobile store scenario consumes, and no rung forbids it.

So an L1 label, read naively, promises more than the check delivers: it does not
mean "no ground-truth pose exists anywhere in this dataflow."

## Decision

**The rung governs which topics a POLICY path may consume. It is not a claim
that ground truth is absent from the dataflow.** Concretely:

1. `poses` (and at L2 `seg_overhead`) are forbidden to every node at their rung.
   This is what VAL-8 enforces.
2. `oracle_state` remains available to `verifier-*` nodes at every rung. This is
   deliberate and is the whole point of VAL-6's exemption: VER-1..3 judge an
   episode against privileged state, and a verifier that could not see ground
   truth could not judge. Holding the answer key is the verifier's job.
3. `base_pose` is out of scope for the ladder. TC-9's ladder is about OBJECT
   pose for manipulation; base localisation is a separate capability with its
   own (unwritten) ladder. Recorded here so its absence is a decision rather
   than an oversight.

## Why not widen the table to `oracle_state`

Considered and rejected: adding `oracle_state` to the L1/L2 tuples with a
`verifier-*` carve-out.

- It would make VAL-8 mirror VAL-6's exemption, and VAL-8's own test suite
  currently pins the OPPOSITE (`test_perception_rung_grants_verifiers_no_exemption`)
  precisely because copying that carve-out across is the plausible future
  mistake. Two adjacent checks with contradictory verifier semantics is worse
  than one clear rule per check.
- Every existing graph wiring `oracle_state` to a verifier would then depend on
  the carve-out being exactly right, for no gain: VAL-6 already restricts that
  topic to verifiers, so a POLICY node cannot reach it either way.

The residual risk this leaves is a `verifier-*` node that launders ground truth
back to a policy. That is not a rung problem; it is the VAL-6 boundary, and a
verifier's outputs are `episode_result`/`verifier_stages` — no pose channel.

## Consequence

An L1 result means: **the policy path estimated pose from pixels; the verifier
still judged with privileged state.** That is the intended experimental design
(VER-3's asymmetry depends on it), and it is what an L1 number should be read to
claim — no more. Any published L1 figure citing TC-9 inherits this reading.
