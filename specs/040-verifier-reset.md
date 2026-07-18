# SPEC 040 — Verifier and reset nodes

Status: DRAFT until M0. Frozen set after M0. Modules: `src/aisle/verifier/`, `src/aisle/reset/`.

Oracle verifier:
- VER-1: Subscribes `oracle_state` + `episode_goal`; publishes `episode_result` per TC-7/8. Judge logic is a pure function `judge(oracle_state, target_idx, t, cfg) -> (status, failure)` — importable and unit-testable without dora or sim.
- VER-2: Success = target box AABB-inside tray volume AND upright within 30° AND robot within home tolerance. Toppled-but-inside-and-upright-within-30° counts as SUCCESS (pre-decided; see design doc §8.3 pitfalls). All thresholds in `verifier/thresholds.toml`.
- VER-3: Failure taxonomy exactly: wrong_object, dropped, timeout, never_grasped, collision. `wrong_object` triggers the moment ANY non-target box enters the tray (safety asymmetry — do not wait for timeout).
- VER-4: The verifier is the ONLY permitted consumer of `oracle_state` (enforced by VAL-6).

Realistic verifier (Phase 2):
- VER-5: Detector+segmentation per-camera verdicts fused with AND; same `episode_result` schema with `verifier:"realistic"`. Runs on MPS (CON-1). Model weights pinned by hash in `verifier/models.lock`.
- VER-6: Fidelity job: `harness/fidelity.py` replays N episodes through both verifiers and reports agreement, false-success and false-fail rates.

Reset node:
- RST-1: Teleport reset: delegate to bridge (BRG-4); MUST complete <2 s.
- RST-2: Behavioral reset: command the robot to return the target box to a sampled shelf pose, verify with the realistic verifier, retry ≤3, then fall back to teleport with `fallback:true` in reply metadata (never hang the loop).

Acceptance: `tests/unit/test_judge.py` — table-driven cases for every VER-3 class + VER-2 edge poses (≥20 cases, cites VER-1..3); `tests/graph/test_verifier_wiring.py::test_oracle_only_edge` (VER-4); `tests/sim/test_behavioral_reset.py` (RST-2, marker sim, Phase 2 gate).
