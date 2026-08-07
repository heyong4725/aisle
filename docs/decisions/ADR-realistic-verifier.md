# ADR-realistic-verifier — accepted design (D1–D6 ratified 2026-08-05)

Status: ACCEPTED — D1–D6 ratified by the owner 2026-08-05
(in-session), every recommended option chosen: D1 OWLv2/transformers,
D2 CPU inference for verifier models, D3 pinned HF snapshot + sha256 in
the env attestation, D4 desk tier T1 first increment (amended
2026-08-05, PR #89 review: 3D from OVERHEAD depth only — see D4), D5
the full VER-6 fidelity contract, D6 segmentation-assisted upright.
Increment one unlocks the T1 fidelity number and ablation A7 and lays
groundwork for perception rung L2; tier T2 is unlocked only by the
SECOND (OCR) increment, which is sequenced on the first fidelity result
and is NOT part of this acceptance. Implementation follows the three-PR
sketch below (spec-change first); note D2's CPU choice was reaffirmed
independently of ADR-26's statistical-outcome ratification — a VERDICT
source must not flicker even where episode outcomes are statistical.
Scope: design doc §8.3 item 1 and §4.2; SPEC 040 VER-6 (fidelity job)
is the governing requirement; the scope statement above (T1 fidelity +
A7 now, L2 groundwork, T2 only with the OCR increment) is the single
authoritative one.

## What it must judge (not just detect)

The oracle's verdict (SPEC 040) is a compound geometric judgment:
correct object IDENTITY inside the TRAY VOLUME (3D containment),
UPRIGHT, robot home. An open-vocab detector alone provides identity
and a 2D box — it cannot judge 3D containment or uprightness. The
realistic pipeline is therefore (PR #89 re-review: the full oracle
verdict, not a camera-only subset):

0. **Calibration contract** — back-projection needs the overhead
   camera's intrinsics and camera-to-base extrinsics. The image/depth
   topics carry only h/w/enc, so the SPEC 040 amendment MUST define an
   ATTESTED calibration config: in sim it is exported from the frozen
   scene's camera construction (position/look-at/FOV — already
   env-hashed with the scene); on hardware the same file carries
   measured calibration. The judge reads calibration ONLY from this
   attested source.
1. **Identity** — open-vocabulary detection on the rendered frame,
   per camera (overhead RGB + wrist RGB).
2. **3D localization** — back-project the overhead detection through
   the calibration contract to test tray-volume containment in the
   robot base frame (only `depth_overhead` exists — the wrist camera
   is RGB-only in SPEC 010, the bridge, and the frozen graphs).
3. **Pose/upright** — segmentation-based extent (mask + overhead
   depth) or a depth-profile heuristic; this is the component D6
   decides.
4. **Robot home** — from `joint_state` against the home-pose
   threshold, the SAME signal and threshold the oracle uses for VER-2;
   cameras play no part in this component.

The final Boolean is explicit:
`verdict = identity_overhead AND identity_wrist AND
containment_overhead AND upright_overhead AND home_joint_state`
(identity fuses across both cameras per the ENPIRE recipe;
containment/upright are overhead-only per the D4 amendment). T2's
label-text-only identification additionally needs OCR/text grounding —
explicitly a SECOND increment (see D4), not smuggled into this one.

## Governance reality (why this needs sign-off)

`src/aisle/verifier/` is IN THE FROZEN SET (CON-7, env-hashed):
implementation is an env-change PR with human review, env_hash regen,
and a new trusted-gate baseline. All in-flight campaigns pin pre-change
commits; sequencing after the H3 campaign is natural.

## Decisions

### D1 — identity detector
- **(a) OWLv2 via transformers — RATIFIED.** Checkpoint
  `google/owlv2-base-patch16-ensemble` (the single ratified identity;
  exact HF revision + sha256 pinned at implementation per D3), classes
  `Owlv2Processor`/`Owlv2ForObjectDetection` (per the official
  transformers docs). True free-text queries, which the T2 increment
  will need. CORRECTED (PR #89 review): `torch` is in the sim extra
  but `transformers` is NOT — the env-change PR adds it (CON-1: no
  CUDA-only pulls) alongside the weights pin. License terms verified
  at pin time.
- (b) YOLO-World via ultralytics: faster, but AGPL-3.0 packaging and
  load-time vocabulary.
- (c) Grounding-DINO class: strongest grounding, heaviest on this
  hardware; overkill for 5 box classes on clean renders.

### D2 — determinism policy (CON-5)
- **(a) CPU inference for all verifier models — RATIFIED.** The
  powder spike showed CPU bit-exact vs Metal nondeterministic; a
  verdict source must not flicker across replays. Judging only
  end-of-episode frames (plus sparse checkpoints) keeps the cost
  bounded. SUBSTANTIATED BY TEST (PR #89 re-review): the env-change PR
  MUST include a determinism replay test — every verifier model
  (detector, segmenter) run twice on the same golden frames within a
  process and across two processes, asserting BIT-identical raw
  outputs (logits/boxes/masks), so the no-flicker guarantee is pinned
  against torch/transformers upgrades rather than assumed from the
  powder-spike precedent.
- (b) MPS with a tolerance band: faster, softer reproducibility for
  the one node whose output is judgment.

### D3 — weights provenance and attestation
- **(a) Pinned snapshot (exact HF revision) fetched at setup +
  sha256 digest recorded in the env attestation — RATIFIED.**
  Unpinned hub downloads are an unattested judgment channel (issue
  #38). License compliance verified at pin time.
- (b) Vendored weights via LFS: simplest attestation, largest repo
  cost.

### D4 — scope of the first increment
- **(a) Desk tier T1 — RATIFIED, amended 2026-08-05 (PR #89 review):**
  identity detection on BOTH cameras with per-camera AND-fusion (the
  ENPIRE recipe), 3D containment + upright from OVERHEAD depth only —
  the wrist camera publishes no depth, and adding `depth_wrist` would
  be a Class-C stable-topic-contract change (SPEC 010 + frozen
  bridge/manifest/graphs + BRG-2 render-rate re-check). `depth_wrist`
  is a NAMED follow-up, taken only if the D5 per-stage disagreement
  breakdown shows the containment/upright stages dominating the
  disagreements (a criterion measurable from overhead-only evidence —
  PR #89 re-review: aggregate rates and identity votes alone could
  not attribute disagreements to 3D). Smallest
  frozen-set change that yields a fidelity number and unlocks A7.
  OCR/label-reading (T2) is increment two, sequenced on the first
  fidelity result (the §7 rendered-label legibility risk may force a
  scene font/texture pass first).
- (b) Desk + retail in one change: drags D1 toward heavier models and
  couples two verifier problems.

### D5 — fidelity reporting contract (VER-6)
- **(a) VER-6's `harness/fidelity.py` shape in full — RATIFIED,
  amended 2026-08-05 (PR #89 re-review):** replay N episodes through
  BOTH verifiers and report agreement, **false-success rate**
  (realistic says success, oracle says fail — the dangerous direction
  for A7), and **false-fail rate**, plus a per-episode disagreement
  log carrying PER-STAGE votes and measurements — identity per camera,
  containment, upright, home, each with its stage measurement
  (score/margin) — so disagreements are attributable to a stage and
  the D4 `depth_wrist` trigger is measurable. Per-run manifests carry
  the three scalars.
- (b) Scalar agreement only: cheaper, loses exactly the asymmetry
  VER-6 exists to expose.

### D6 — upright/pose component
- **(a) Segmentation-assisted (MobileSAM-class mask + depth extent) —
  RATIFIED** for robustness on tilted/occluded boxes; same CPU and
  attestation rules as D1/D3.
- (b) Depth-profile heuristic only (no second model): lighter, likely
  brittle exactly where uprightness matters (leaning boxes) — could be
  increment one with (a) as the upgrade if fidelity says so.

## Implementation sketch (after acceptance, ~1 PR each)

1. Spec-change PR: SPEC 040 amendment (VER IDs for the realistic
   pipeline + the D5 contract) — no code. MUST define: the attested
   calibration contract (stage 0), the joint_state home predicate
   (stage 4, same threshold as the oracle's VER-2), the explicit
   Boolean fusion above, and the per-stage disagreement record.
2. Env-change PR: `verifier/realistic.py` (the five-stage judge —
   calibration, per-camera identity, overhead containment, overhead
   upright, joint_state home — with the explicit Boolean fusion), the
   `transformers` dependency added to the sim extra, weights fetch +
   attestation, env_hash regen, golden fidelity tests on recorded
   frames, and the D2 determinism replay test (below).
3. Harness PR: `harness/fidelity.py` (VER-6), `--verifier both`
   plumbing of the three scalars + disagreement log into manifests.

## Risks stated plainly

- Rendered-label legibility (§7) gates the T2/OCR increment.
- A low fidelity number is a RESULT, not a failure — but it blocks A7
  until understood.
- CPU judging adds wall time to every `both` rollout; budget ~+10–30 s
  per episode at end-of-episode judging (measure in increment one).

IDs: VER-6 (fidelity contract), CON-5 (D2), CON-7 (frozen-set
process), CON-1 (no CUDA-only deps), CON-15 (this ADR).

## Addendum (spec-change PR #101, CON-15): hardware calibration artifact

Phase 4's measured calibration enters the VER-8 contract through a
dedicated per-device artifact, `env/calibration.toml` — NOT the sim
scene files. It carries the same v1 fields/conventions as the sim
block (SPEC 040 VER-8); acquisition and maintenance follow the same
owner sign-off discipline as other hardware-provenance assets. ADR-6
records simulator/asset provenance only and is not the governing
reference for this artifact.
