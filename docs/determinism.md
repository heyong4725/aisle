# Determinism notes (SCN-7, CON-5)

Contract: same (seed, cfg, platform) ⇒ bitwise-identical initial
`oracle_state`. Verified by `tests/sim/test_scene.py::test_build_determinism`
on macOS arm64 (Metal backend, float32).

Known platform caveats — recorded here rather than hidden (SCN-7):

- Initial oracle_state is placement-derived (pure Python RNG → float32), so
  it is expected to be bitwise-identical across backends. POST-STEP state is
  not yet covered by any contract: Metal vs CUDA vs CPU floating-point
  reduction order may diverge once physics steps run (relevant from T05
  onward; measure before promising cross-platform reproducibility).
- genesis is initialized once per process (backend fixed at first
  build_scene call); mixing backends in one process is unsupported.

No Metal-vs-CUDA divergence has been measured yet — no CUDA machine in the
loop. This file is the place to record it when one appears.
