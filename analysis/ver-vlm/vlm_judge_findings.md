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
