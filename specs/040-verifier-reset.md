# SPEC 040 — Verifier and reset nodes

Status: DRAFT until M0. Frozen set after M0. Modules: `src/aisle/verifier/`, `src/aisle/reset/`.

Oracle verifier:
- VER-1: Subscribes `oracle_state` + `episode_goal`; publishes `episode_result` per TC-7/8. Judge logic is a pure function `judge(oracle_state, target_idx, t, cfg) -> (status, failure)` — importable and unit-testable without dora or sim.
- VER-2: Success = target box AABB-inside tray volume AND upright within 30° AND robot within home tolerance. Toppled-but-inside-and-upright-within-30° counts as SUCCESS (pre-decided; see design doc §8.3 pitfalls). All thresholds in `verifier/thresholds.toml`.
- VER-3: Failure taxonomy exactly: wrong_object, dropped, timeout, never_grasped, collision. `wrong_object` triggers the moment ANY non-target box enters the tray (safety asymmetry — do not wait for timeout).
- VER-4: The verifier is the ONLY permitted consumer of `oracle_state` (enforced by VAL-6).

Realistic verifier (Phase 2; increment one = desk T1 — ADR-realistic-verifier, D1–D6 ratified 2026-08-05):
- VER-5 (AMENDED by ADR-realistic-verifier): the realistic judge is a FIVE-STAGE pipeline — (0) calibration contract, (1) per-camera identity, (2) overhead-depth 3D containment, (3) segmentation-assisted uprightness, (4) joint_state home — publishing the same `episode_result` schema with `verifier:"realistic"`. ALL verifier model inference runs on CPU (D2; supersedes this spec's earlier "runs on MPS" — a verdict source must not flicker across replays, and Metal inference is nondeterministic). Weights: pinned HF snapshot revision + sha256 recorded in `verifier/models.lock` AND carried into the env attestation (D3, ADR-24). The realistic verdict is the Boolean success bit plus the VER-6b stage record; VER-3 failure-class attribution remains the oracle's — the realistic `failure` field is informative, never compared classwise.
- VER-5a (stage 0, calibration contract): the camera intrinsics/extrinsics and depth scale used by stages 2–3 derive from the scene layout (SCN-2 single source) and are hashed into the env attestation; an attested-calibration/scene-build mismatch REFUSES to judge (fail closed) instead of judging in a wrong frame.
- VER-5b (stage 1, identity): OWLv2 (`google/owlv2-base-patch16-ensemble`, D1) free-text queries per med class, run per camera (overhead RGB and wrist RGB); a camera's identity vote is success iff the TARGET class detects inside the tray region at ≥ the score threshold in `verifier/thresholds.toml`. Per-camera votes are recorded individually (feeds VER-6b).
- VER-5c (stage 2, containment): tray-volume containment from OVERHEAD depth only (D4 — the wrist camera publishes no depth; `depth_wrist` is a NAMED follow-up taken only if VER-6b's per-stage disagreement breakdown shows stages 2–3 dominating).
- VER-5d (stage 3, upright): segmentation-assisted uprightness (MobileSAM-class mask + depth extent, D6) with the SAME 30° threshold as VER-2.
- VER-5e (stage 4, home): joint_state within the SAME home tolerance as VER-2 — one threshold, one source (`verifier/thresholds.toml`).
- VER-5f (fusion, explicit and Boolean): realistic success iff identity_overhead AND identity_wrist AND containment AND upright AND home. A stage unable to produce a verdict (missing frame, calibration refusal, model error) makes the episode's realistic verdict FAIL with that stage recorded as `error` in VER-6b — fail closed, never skip-and-fuse.
- VER-6 (AMENDED, D5): `harness/fidelity.py` replays N episodes through BOTH verifiers and reports agreement, FALSE-SUCCESS rate (realistic success where the oracle failed — the dangerous direction for A7), and false-fail rate; per-run manifests carry the three scalars.
- VER-6b (per-stage disagreement record, D5): every disagreement logs per-stage votes AND the stage measurements (identity score per camera, containment margin, upright angle, home residual) so each disagreement is attributable to a stage; this record is the decision evidence for the D4 `depth_wrist` trigger.
- VER-7 (determinism replay, D2): every verifier model is run twice on committed golden frames within one process AND across two processes; the raw outputs (logits/boxes/masks) MUST be bit-identical. This test pins the no-flicker guarantee against torch/transformers upgrades rather than assuming it from the powder-spike precedent.

Reset node:
- RST-1: Teleport reset: delegate to bridge (BRG-4); MUST complete <2 s.
- RST-2: Behavioral reset: command the robot to return the target box to a sampled shelf pose, verify with the realistic verifier, retry ≤3, then fall back to teleport with `fallback:true` in reply metadata (never hang the loop).

Acceptance: `tests/unit/test_judge.py` — table-driven cases for every VER-3 class + VER-2 edge poses (≥20 cases, cites VER-1..3); `tests/graph/test_verifier_wiring.py::test_oracle_only_edge` (VER-4); `tests/sim/test_behavioral_reset.py` (RST-2, marker sim, Phase 2 gate). Realistic increment one (env-change PR, ADR-realistic-verifier sketch step 2): fusion truth table incl. the fail-closed stage-error rows (VER-5f) and calibration refusal (VER-5a) as pure unit tests; golden-frame fidelity fixtures for VER-5b–5e; the VER-7 replay test; VER-6/6b shapes covered by `harness/fidelity.py` unit tests (sketch step 3).
