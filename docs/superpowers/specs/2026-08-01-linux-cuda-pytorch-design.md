# Local Linux CUDA PyTorch design

## Goal

Switch this checkout's existing Linux virtual environment from CPU-only
PyTorch to the official PyTorch 2.13.0 CUDA 13.0 wheel so this workstation's
RTX 5090 can run Genesis simulations. Do not install, replace, or otherwise
modify the host NVIDIA driver or system CUDA toolkit.

## Dependency design

- Keep `pyproject.toml` and `uv.lock` unchanged. CON-1 forbids CUDA-only
  packages in the default dependency set and permits them only behind an
  optional `cuda` extra; the owner requested no such extra.
- Replace only this checkout's installed CPU torch distribution using
  `uv pip install` and the official
  `https://download.pytorch.org/whl/cu130` index.
- Accept CUDA runtime libraries installed inside AISLE's `.venv` as wheel
  dependencies. These are project-local and must not alter system packages,
  the NVIDIA driver, or `/usr/local/cuda`.

This deliberately makes CUDA mandatory in this checkout's current `.venv`
without changing the repository's portable dependency contract. A later
`uv sync --extra sim` will restore the locked CPU distribution; after such a
sync, this local override must be reapplied.

## Safety and failure handling

No command may invoke an NVIDIA driver installer, an OS package manager, or a
system CUDA installer. If the official CUDA wheel cannot use the existing
driver, stop and restore CPU dependency resolution rather than changing the
host. Existing unrelated Python environments remain untouched.

Environment installation is restricted to this repository's `.venv`.

## Verification

Verification succeeds only when all of the following are true:

1. PyTorch reports a CUDA build, `torch.cuda.is_available()` is true, and device
   0 identifies the RTX 5090.
2. A small tensor operation executes on device 0 and returns the expected
   result.
3. The expert T0 graph validates.
4. The unit suite passes with the host ROS pytest plugins isolated.
5. A one-episode T0 rollout succeeds and produces trace/video artifacts.
6. No dora or Genesis processes remain after verification.

The previously measured CPU rollout provides the comparison baseline:
104.6 seconds wall time for 27.4 simulated seconds on seed 0.
