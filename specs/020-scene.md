# SPEC 020 — Pharmacy scene (Genesis World)

Status: DRAFT until M0. Frozen set after M0 (CON-7). Module: `src/aisle/scenes/pharmacy.py`.

- SCN-1: `build_scene(seed:int, embodiment:str="franka", n_envs:int=1, headless:bool=True, cfg:SceneCfg|None=None) -> SceneHandle` MUST be a pure function of its arguments (CON-5). `SceneHandle` exposes: `scene`, `robot`, `boxes: dict[str, Entity]` (insertion order = oracle_state order, TC table), `tray`, `cams: dict[str, Camera]`.
- SCN-2: Five medicine boxes (names fixed: amoxicillin, ibuprofen, cetirizine, omeprazole, metformin); sizes/colors from `scenes/meds.toml`, physics params from `scenes/physics.toml`. NO physics constants inline in code.
- SCN-3: Box placement randomized per seed on a staggered **two-level** shelf (M0 env-change, owner-approved; see ADR-12 — the original three-level stack physically blocked the top-down grasp on lower levels, making M0-1 unreachable); rejection-sample any initial interpenetration; placements MUST be reachable (IK solution exists) — assert at build time.
- SCN-4: Embodiments: `franka` (Genesis-bundled MJCF) and `so101` (URDF under `assets/so101/`); both MUST place the tray and shelf inside the arm's workspace (asserted).
- SCN-5: Cameras: `overhead` 640x480 fov 55 fixed; `wrist` 320x240 fov 70 attached to EE link. Rendering MUST use the rasterizer path by default (Metal-safe, CON-1).
- SCN-6: Domain-randomization toggles (lighting, textures, friction jitter, camera jitter) behind `SceneCfg`, all default OFF; each toggle independently seedable.
- SCN-7: Determinism: same (seed,cfg,platform) ⇒ bitwise-identical initial `oracle_state`; document any Metal-vs-CUDA divergence in `docs/determinism.md` rather than hiding it.

Acceptance: `tests/sim/test_scene.py::test_build_determinism` (SCN-1,7), `::test_reachability` (SCN-3,4), `::test_no_interpenetration` (SCN-3), `tests/unit/test_scene_cfg.py` (SCN-2,6 — config parsing only, no sim).
