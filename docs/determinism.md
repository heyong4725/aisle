# Determinism notes (SCN-7, CON-5)

Contract: same (seed, cfg, platform) ⇒ bitwise-identical initial
`oracle_state`. Verified by `tests/sim/test_scene.py::test_build_determinism`
on macOS arm64 (Metal backend, float32).

Backend selection is automatic: macOS uses Metal; other platforms use CUDA
when PyTorch reports an available CUDA device and otherwise use CPU. Linux
x86-64 CUDA execution has been exercised on an NVIDIA GeForce RTX 5090 with
driver 580.126.09 and PyTorch 2.13.0+cu130.

Selection is only half the story: `uv sync --extra sim` resolves the CPU
torch on Linux (CON-1), so `cuda_available` is False and the CUDA branch
never engages. The GPU build comes from the `cuda` extra —
`uv sync --extra cuda` — which is the sanctioned home for CUDA wheels and
is mutually exclusive with `sim`. Running `--extra sim` on a GPU host is
therefore a silent CPU run, not a broken one.

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
