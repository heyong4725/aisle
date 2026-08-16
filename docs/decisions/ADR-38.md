# ADR-38 — VLA chunk preemption and the SmolVLA bring-up scope

Status: ACCEPTED 2026-08-17 (owner ratified next-phases.md and directed
the SmolVLA bring-up; the plan requires this rule specified BEFORE the
first motion inference runs).

## Chunk preemption rule (Phase 5.1)

A `vla-policy` node emits ACTION CHUNKS (a bounded sequence of joint
targets from one inference). The rule, enforced in the node and stated
in its manifest:

1. **One in-flight chunk.** A new inference result REPLACES the queued
   remainder of the previous chunk at the next action boundary — never
   interleaves with it. Two policies (or two inferences) can never
   fight over the arm.
2. **The guard is unchanged and non-negotiable.** Every chunk element
   traverses budget-guard clamping exactly like classical joint_cmd;
   preemption is a policy-side queueing rule, not a safety mechanism.
3. **Reset flushes.** `reset_done` drops any queued chunk (the episode
   boundary rule every stateful node follows).
4. **Inference staleness floor.** A chunk computed against observations
   older than `VLA_STALE_NS` (default 500 ms sim time) at emission time
   is dropped, not executed — a slow inference must never act on a
   world that has moved on.

## Bring-up scope (honest expectations)

SmolVLA (lerobot `smolvla_base`, ~450M) is trained on SO-100/SO-101
tabletop data. Zero-shot on the Genesis pharmacy scene — even the
SO-101 profile — is EXPECTED to score at or near 0: the bring-up
deliverable is the typed integration (node, manifest, eval graph,
preemption rule, dependency extra) plus the honest zero-shot baseline
as M1's first data point, NOT a working policy. Fine-tuning on
expert-graph demonstration data is the follow-on (its own idea-gated
measurement); claims of VLA value await it. The dependency rides an
optional `vla` extra (CON-1: nothing CUDA-only in defaults; MPS/CPU
inference). Model identity: weights pinned by HF revision hash in the
manifest (the env-hash discipline extended to weights, next-phases §5).
