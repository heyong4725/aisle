# Pre-registered: SmolVLA fine-tune on expert demonstrations (COMPUTE-GATED)

Registered 2026-08-17 BEFORE any training run (arc 3 of the
owner-directed trio; the idea-gate discipline applied to a training
job). Status: **awaiting the owner's GPU/compute decision** — nothing
below runs until then.

- **Dataset:** successful expert_t1 episodes exported by
  `tools/demo_export.py` (guard-clamped actions only; causal
  frame-action pairing test-pinned). Target: 50 episodes (~1 rollout
  campaign at A-series budgets) across dev seeds 0..49.
- **Training:** lerobot SmolVLA fine-tune from the pinned base
  (c83c3163), single config, no sweep: the first contrast is
  fine-tuned-vs-zero-shot, not hyperparameter search.
- **Evaluation:** graphs/eval_vla_smolvla_t1.yaml UNCHANGED, seeds
  30..37 (the zero-shot suite) — success = pass@1 strictly above the
  0/4 baseline with wrong_object 0; the evalcard updates to whatever is
  measured. Held-out seeds 100..107 only if dev shows life.
- **Expectations, stated now:** T1 may become nonzero (in-domain
  demonstrations, same camera); T2 remains out of reach without
  label-conditioned data. wrong_object must stay 0 — a fine-tuned
  motion policy is H5's hardest test (M5) and any wrong-medicine event
  halts the arc.
- **Compute ask (owner decision):** MPS-only training of 450M is
  borderline; the honest ask is a CUDA host (the `cuda` extra exists)
  or a small cloud budget. SmolVLA LoRA on MPS is the fallback
  experiment if no GPU materializes.
