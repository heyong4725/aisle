# Genesis Linux CUDA backend design

## Goal

Make AISLE automatically run Genesis on CUDA when CUDA-capable PyTorch and a
working NVIDIA GPU are available, while preserving Metal on macOS and CPU
operation on Linux hosts without CUDA. The owner explicitly authorizes this
Class C change to the frozen scene path.

## Backend selection

Backend selection will be a pure helper in `src/aisle/scenes/pharmacy.py`:

- Darwin selects `metal`.
- A non-Darwin platform with `torch.cuda.is_available() == True` selects
  `cuda`.
- A non-Darwin platform without available CUDA selects `cpu`.

The helper will return a backend name rather than importing Genesis, allowing
unit coverage without loading simulator dependencies. `_ensure_genesis()` will
map that name to `gs.metal`, `gs.cuda`, or `gs.cpu`.

Genesis remains initialized once per process. If it was already initialized
with a different backend, `_ensure_genesis()` will fail loudly rather than
silently changing the physics backend.

## Compatibility and safety

The automatic CPU fallback keeps the repository's locked CPU-only Linux
environment functional and preserves CON-1. macOS behavior remains unchanged.
No dependency metadata, NVIDIA driver, system CUDA toolkit, graph, verifier, or
reset implementation will change.

CUDA availability is determined by PyTorch at process startup. A CUDA runtime
error during `gs.init()` must propagate; it must not trigger a silent retry on
CPU.

## Determinism and reporting

Initial placement-derived oracle state remains expected to match across
backends. Post-step CUDA state may diverge from Metal or CPU because floating
point reductions differ. `docs/determinism.md` will record CUDA as an active,
measured backend and identify the verified GPU/driver combination.

The current `bridge_info.platform` contract is unchanged because BRG-6 fixes
its exact schema. Backend evidence for this local verification will come from
the PyTorch device check, `nvidia-smi` process sampling, and run manifest
platform/environment fields.

## Tests and acceptance

1. Unit tests must fail before implementation and then cover Darwin/Metal,
   CUDA-capable Linux/CUDA, and non-CUDA Linux/CPU selection.
2. Existing unit tests must pass.
3. A simulator process must appear in `nvidia-smi` with allocated device
   memory while running.
4. A one-episode expert T0 rollout must complete successfully and emit traces
   and video.
5. No dora or Genesis processes may remain after verification.

