# SPEC 200 — Retail competition suite (S1–S3)

Status: DRAFT, post-M0 (do not implement before SPEC 090 sign-off). Class B.
Design-doc anchor: §11. Depends on: SPEC 010/210 (contract), 020 (scene ext),
040 (verifier ext), 050 (registry ext), 070 (rollout tiers).

## Shared infrastructure

- RS-1: `scenes/planogram.toml` maps every shelf slot id to {category, template_pose(7d, shelf frame), capacity, shelf_zone}. The store scene (RS-2) is GENERATED from it; verifiers query it. One source of truth.
- RS-2: Store scene extends SPEC 020: ≥3 shelf units in ≥2 aisles, delivery counter, restocking bin, navigable free space ≥0.9 m aisle width. `build_store(seed, scenario, embodiment="mobile", ...)` reuses `build_scene` conventions (SCN-1 purity, toml-driven assets).
- RS-3: Episode generator per scenario, seeded: S1 samples an order (2 product types, qty 1..3 each, spec disambiguators); S2 removes stock from 2 random slots; S3 swaps 2 items against the planogram. Generator output is the episode's oracle task description, published per TC-7 goal.
- RS-4: Placement score (design doc §11.3): pos ≤2 cm to template, yaw ≤10°, front-face outward, zero shelf-edge overhang, neighbor front-edge alignment ≤1.5 cm. Thresholds in `verifier/placement.toml`. Verifier reports per-criterion pass/fail. New failure classes: misplaced, misaligned, overhang, wrong_slot, missing_item, extra_item.
- RS-5: One parameterized retail verifier: `judge_retail(oracle_state, planogram, episode_goal, t, cfg)` — pure function (VER-1 discipline). Scenario differences are goal parameters, not verifier forks.
- RS-6: Scoring per episode: {success: bool, t_end, penalties: [class...], placement_scores: [...]}. Rollout (HAR-1) gains `--tier S1|S2|S3`.

## Per-scenario success (normative)

- RS-7: (S1) success iff every ordered (product, spec, qty) is on the counter at t_end AND no non-ordered product is on the counter. Wrong item on counter at any time ⇒ immediate `extra_item` failure (safety asymmetry, VER-3 spirit).
- RS-8: (S2) success iff both assigned slots hold an item of the assigned category passing RS-4 placement.
- RS-9: (S3) success iff both misplaced items are in their planogram slots passing RS-4, AND their origin slots are not newly wrong.

## Oracle/realistic ladder

- RS-10: Oracle rung: order JSON, stock state, and misplacement list published directly from scene state. Realistic rung: order-reader (OCR/VLM on rendered slip), stock-detector, misplacement-detector per SPEC 050 additions. Ladder config per HAR rollout flag; fidelity reporting per VER-6 pattern.

## Acceptance

- tests/sim/test_store_scene.py::test_planogram_generation (RS-1,2), ::test_episode_generators_seeded (RS-3)
- tests/unit/test_judge_retail.py — table-driven: ≥10 cases per scenario incl. every RS-4 criterion failing alone (RS-4..9)
- tests/accept/test_s1_expert.py::test_scripted_order_pick — hand-written expert graph completes a fixed-seed S1 episode (integration gate for the suite, analogous to M0-1)

## Out of scope
SLAM (nav is waypoint-based against known map, RS via SPEC 210); dynamic obstacles; multi-robot; humanoid embodiment (mobile base + arm only).
