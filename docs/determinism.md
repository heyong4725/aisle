# Determinism notes (SCN-7, CON-5)

Contract: same (seed, cfg, platform) ⇒ bitwise-identical initial
`oracle_state`. Verified by `tests/sim/test_scene.py::test_build_determinism`
on macOS arm64 (Metal backend, float32).

Backend selection is explicit and recorded. `uv sync --extra sim` plus
`harness rollout --sim-extra sim` uses Metal on Darwin and CPU elsewhere,
even when a CUDA device is visible. `uv sync --extra cuda` plus
`harness rollout --sim-extra cuda` uses CUDA on Linux and fails closed when
the device is unavailable; it never silently retries on CPU. The selected
extra, Genesis backend, and device are persisted in `manifest.json` beside
the environment fingerprint. Linux x86-64 CUDA execution has been exercised
on an NVIDIA GeForce RTX 5090 with driver 580.126.09 and PyTorch
2.13.0+cu130.

Known platform caveats — recorded here rather than hidden (SCN-7):

- Initial oracle_state is placement-derived (pure Python RNG → float32), so
  it is expected to be bitwise-identical across backends. POST-STEP state is
  not yet covered by any contract: Metal vs CUDA vs CPU floating-point
  reduction order may diverge once physics steps run (relevant from T05
  onward; measure before promising cross-platform reproducibility).
- genesis is initialized once per process (backend fixed at first
  build_scene call); mixing backends in one process is unsupported.
- CUDA startup errors propagate; AISLE never silently retries initialization
  on CPU. Metal-vs-CUDA post-step divergence has not yet been quantified.
