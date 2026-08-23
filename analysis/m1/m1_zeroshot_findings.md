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

## CORRECTION (2026-08-20): the 0/4 measured a crashed node, not the model

The fine-tune debug traced two load failures and invalidated this
file's mechanism claim. In the recorded zero-shot run the vla node's
first inference attempt died on `ImportError: num2words` (node log,
run 01a00bf7…), so **no SmolVLA inference ever executed in the sim** —
"typed integration proven end-to-end" was wrong; frames→chunks→guard
was proven only up to the (dead) inference call. Separately, lerobot's
default MPS device placement crashes natively at weight loading
(silent kill, leaked semaphore); CPU loads the same 450M cleanly. Both
fixed (num2words pinned in the vla extra; CPU-device load in
vla_backend). The honest baseline number requires a RERUN with a live
policy; until it lands, "zero-shot performance" is UNMEASURED and the
registry evalcard's 0.0 describes an inert node. The rerun supersedes
this section's table when recorded below.

## CORRECTION 2 (2026-08-20): zero-shot is structurally impossible for smolvla_base

The debug's final layer: with the load fixed (MPS crash fenced, hub
config loaded) and the batch built to the real schema
(camera1..3 @ 256², 6-dim SO-100 state), inference refuses with
"`mean` is infinity … initialize with `stats`": **the base checkpoint
ships uninitialized normalization statistics by design** — it is a
pre-training artifact meant to be fine-tuned, which supplies stats
from the tuning dataset. So the M1 "zero-shot" row is structurally 0
for ANY scene, and the fine-tune (protocol as pre-registered, stats
computed from our 86k demonstration tuples) is not an improvement
path but the ONLY path to a live SmolVLA in this loop. Three load/run
bugs fixed en route, each previously masked by a silent-failure
pattern this project has now fenced three times: num2words import
(node died silently → the 0/4 measured an inert node), lerobot MPS
placement (native crash at weight load → CPU fence), empty default
config schema (select_action refuses → hub config required).

## The fine-tuned SO-101 eval: first LIVE policy episodes — 0/8, mechanisms named (2026-08-22)

Training (owner-amended protocol): 800 LoRA steps on 4000 of the 135k
SO-101 demo tuples, CPU, loss 0.041 → 0.0066; adapter + dataset stats
saved (runs/smolvla_lora_so101_v1; ~33 h wall — the per-step mp4 seek
is the bottleneck, pre-decode + checkpointing are the recorded tool
fixes). Eval: graphs/eval_vla_smolvla_so101.yaml, T0/so101, seeds
30..37, adapter + stats live via AISLE_VLA_ADAPTER.

**Result: pass@1 0/8** — 5 never_grasped at full budget, 3 wall_clamp
(episodes at ~5–9 min wall for 60 sim-s). **Liveness PROVEN this
time**: model loaded, zero `[vla-backend] inference refused` lines —
the first episodes in this project where a learned policy actually
inferred in the loop. wrong_object 0; M5 did not halt.

The mechanism is computation speed, not integration: at CPU inference
latency (tens of sim-seconds per chunk in free-run), ADR-38's
staleness floor — working exactly as designed — discards chunks
computed against a world that has moved on, so the arm receives
sparse, mostly-refused actuation. The safety rule and the honest
result are the same fact. Paths forward, in cost order: (a) GPU-peer
inference (the next-phases §5.1 placement — latency to ~100 ms makes
chunks live); (b) lockstep with inference-aware turn budgeting (the
async gate keeps turn_done flowing but the sim still outruns the
model); (c) smaller/distilled policies. The fine-tune itself is
validated as a PIPELINE (loss learned, adapter loads, policy lives);
its task value is unmeasurable until inference fits the control rate.
