# vlm-verifier v1 findings: SmolVLM-500M is not a viable judge here (2026-08-17)

Offline judge (`tools/vlm_judge.py`, next-phases §5.2 v1) over recorded
episodes: SmolVLM-500M-Instruct @ a7da5b98, tail frames decoded from
traces/overhead.mp4 at exact rgb-event indices (frames are elided from
the Arrow numeric path by design), 200 ms reset guard. Two pre-declared
prompt styles, reported side by side.

| run (oracle mix) | prompt | judged | agreement | false_success | false_fail |
|---|---|---|---|---|---|
| ik-transfer reeval (8 success) | semantic | 8 | **0.0** | 0 | 8 |
| ik-transfer reeval (8 success) | calibrated | 8 | 0.375 | 0 | 5 |
| t2-scan-pose r3 (1 succ, 2 collision) | calibrated | 3 | 0.333 | **2** | 0 |

- **Semantic grounding fails completely**: asked about "medicine box"
  and "delivery tray", the model describes the render as "a 3D
  illustration with shapes" and answers no on every visible delivery
  (frame evidence: the red amoxicillin box is plainly on the tray pad).
- **Scene calibration recovers only a third** — and introduces the
  DANGEROUS error class: an identity-free "is a box on the pad"
  question said yes on collision episodes. A judge whose failure mode
  is false_success is disqualified for this project's 10x asymmetry
  regardless of aggregate agreement.
- Verdict: **500M-scale zero-shot VLM judging of this stylized render
  fails in both directions.** The §5.2 path forward, in order of cost:
  (a) larger instruct VLM (2B+) behind the same tool (model id is a
  constant); (b) label-rendered frames (AISLE_LABELS already exists —
  give the judge the text the T2 tier gives the policy); (c)
  fine-tuning. The tool, mp4 frame mapping, refusal accounting, and
  #250 backbone labels are landed and model-agnostic.
- Backbone labels attached per run: SmolVLM is independent of every
  classical pipeline judged here; it is NOT independent of
  vla-policy-smolvla runs (same vision family) — recorded so no future
  fidelity table silently claims independence it lacks (#248/#250).

## The judge bench (ENPIRE follow-up 3 — 2026-08-18)

`tools/judge_bench.py`: the labeled corpus (13 episodes: 8 dev from the
prompt-calibration run — dev forever — and 5 holdout with real failure
mix) plus the promotion gate: **agreement ≥ 0.8 on holdout AND
false_success == 0**. Judge proposals (model, prompt, fusion) iterate
against it offline at zero sim cost; a passing judge still enters live
verification only through the §9.4 evalcarded human-merge path.

Recorded baseline (calibrated prompt, SmolVLM-500M @ a7da5b98):
holdout agreement **0.2**, false_success **4/5**, `passes: false` —
worse on fresh holdout than on dev, confirming both the disqualification
and the necessity of the split. This is the number the 2B+ model,
label-rendered frames, or fine-tuned judge must beat.


## The 2B retry (path (a)) — 2026-08-26: fails, and exposes a gate defect

SmolVLM2-2.2B-Instruct @ 482adb53 behind the same tool (`--model
smolvlm2-2.2b`, both pre-declared prompts, corpus unchanged except the
purged r3 run repointed to the runner's identical copy — manifest
run_id matches):

| prompt | holdout agreement | false_success | success_recall | gate |
|---|---|---|---|---|
| calibrated | 0.6 | **2** | 1.0 | fail |
| semantic | 0.8 | 0 | **0.0** | fail (post-fix) |

- **Calibrated** quadruples the 500M's holdout agreement (0.2 → 0.6)
  and halves false_success (4 → 2), but the two surviving
  false-successes are the same class as before: collision episodes
  where a box IS on the pad and identity-free "is a box on the pad"
  says yes. Disqualified by the 10x asymmetry, as pre-registered.
- **Semantic** answered FAIL on all 13 episodes — including every
  plainly visible delivery — and "passed" the original gate at 0.8
  purely because 4/5 holdout episodes are failures. A constant-fail
  judge with zero discriminative power satisfying the promotion gate
  is a BENCH DEFECT: the gate now also requires success_recall > 0
  (`bench_verdict`, unit-pinned). No recorded verdict changes: the
  500M baseline failed on false_success, not recall.
- Verdict: **2B zero-shot on the stylized render is better but still
  disqualified.** Remaining pre-declared paths: (b) label-rendered
  frames (needs newly recorded runs with AISLE_LABELS — sim time),
  (c) fine-tuning (GPU). The scaling trend (0.2 → 0.6 agreement,
  4 → 2 false_success) suggests (b)+(a) combined is the next cheap
  test once a labeled corpus run is recorded.


## Path (b) measured: label-reading fails at overhead resolution (2026-08-27)

The pre-declared `label` prompt (#326) asks the judge to use the med
name the T2 scenes print on every box. Scored beside both prior styles
on the EXTENDED holdout — 13 episodes after adding the fresh
t2-scope-v2 labeled run (8 episodes, 4 success / 4 fail, never used
for prompt development; goal mapping episode%5, verified against the
registration run's goal log):

| prompt | holdout agreement | false_success | success_recall | gate |
|---|---|---|---|---|
| label | 0.615 | 0 | **0.0** | fail |
| calibrated | 0.538 | **5** | 0.8 | fail |
| semantic | 0.615 | 0 | 0.0 | fail |

- **Label-reading is a nonstarter at this camera geometry**: boxes are
  ~21 px in the 640x480 overhead frame, so the model can never affirm
  "the box printed with '<med>'" — recall 0.0, i.e. the constant-fail
  shape the hardened gate exists to refuse. The failure is optics, not
  prompting: no wording change makes 21 px text legible.
- **Calibrated degrades on the harder mix** (0.6 → 0.538, 2 → 5
  false-successes): more failure episodes with boxes near the tray =
  more identity-free yeses. The dangerous class scales with exactly
  the episodes a deployment would care about.
- The promotion gate has now correctly refused FIVE judge
  configurations (500M-calibrated, 500M-semantic-era, 2B-semantic,
  2B-calibrated, 2B-label) without a single false promotion.

**5.2 closes as**: v1 shipped and gate-hardened; cheap paths (a)
larger model and (b) label prompts both measured and exhausted. The
recorded remainder needs a design change — wrist-camera tail frames
or higher-res judged renders (new recorded runs), or path (c)
fine-tuning (GPU). The bench, corpus, and per-model pins make any of
those a one-command measurement when unlocked.
