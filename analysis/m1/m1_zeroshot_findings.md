# M1 first data point: SmolVLA zero-shot on T1 (2026-08-17)

Per the ratified next-phases §5.1 and ADR-38's pre-declared scope:
**pass@1 0/4** (seeds 30–33, all `never_grasped` at the full 60 s sim
budget; wrong_object 0). lerobot/smolvla_base @ c83c3163, MPS/CPU
inference, free-run graph (`/tmp` staging of
graphs/eval_vla_smolvla_t1.yaml minus lockstep), ADR-25 bring-up route
(`dora run`, `--env-baseline`-free). Exactly the expected result — the
model is trained on SO-100/101 tabletop data and has never seen this
scene, embodiment profile, or camera geometry. The value of the run is
the typed integration proven end-to-end (frames → inference → chunks →
guard → sim) and this baseline for the fine-tuning contrast.

## Machinery findings (each its own lesson)

1. **Lockstep starves on seconds-scale inference participants.** The
   measured graph wedged: ADR-30's turn barrier waits on the VLA's
   turn_done each turn; at ~1–2 s/inference the watchdog overran twice
   and the flow stalled silently for 90+ min. Async inference (the
   GPU-peer placement of next-phases §5.1) or a turn-budget carve-out
   is a PREREQUISITE for measured lockstep VLA runs, not an
   optimization. The zero-shot number therefore comes from the
   free-run bring-up route, clearly labeled.
2. Direct `dora run` bring-ups must launch under `uv run` from the
   repo (nodes spawn with cwd at the YAML's directory; a bare launch
   resolves no project env → ModuleNotFoundError).
3. Kill patterns must never match their own launcher's command line
   (`pkill -f <path>` self-terminated one attempt).

## What the evalcard means

`pass_rate: 0.0` on the registry entry is deliberate and honest: the
manifest exists so T1/T2 graphs can VALIDATE with the node (M1's A/B
needs it composable), and the card records what zero-shot measures
today. Any campaign agent routing through this node sees the number.
Fine-tuning on expert-graph demonstrations is the follow-on that could
move it; claims of VLA value wait for that measurement.

## Hybrid fallback mechanism (ENPIRE follow-up 2 — 2026-08-18)

ENPIRE's RoboCasa synergy (VLA + procedural tools composed) lands as a
graph-attested mode: `AISLE_VLA_FALLBACK` puts the classical/VLA
arbitration in the task-state-machine — first retry stays classical,
later retries route `vla_request` to the VLA branch; mutually exclusive
by construction (one decision node, never two writers to the arm), both
branches through the budget guard. `graphs/eval_hybrid_t1.yaml` is the
first VLA-bearing graph through the FULL gated rollout path (the 0.0
evalcard makes it validator-legal — the honest card doing its job).

No-regression measurement (run hybrid-t1-noregression-r2, seeds
30..37): **7/8**, one `collision` (the documented flake class), zero
retries — the fallback never engaged on T1, as designed (no headroom),
and the silent VLA branch cost nothing. wrong_object 0.

Design note recorded: episode-ENDING failures (collision, dropped,
wrong_object) bypass the retry path entirely — the fallback only ever
sees in-flight failures (`never_grasped` class). The value contrast
(M1 hybrid vs classical where classical FAILS) awaits a VLA worth
falling back to: the compute-gated fine-tune.
