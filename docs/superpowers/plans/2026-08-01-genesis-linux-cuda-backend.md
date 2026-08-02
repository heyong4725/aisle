# Genesis Linux CUDA Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Genesis automatically select CUDA on CUDA-capable Linux hosts,
while retaining Metal on macOS and CPU fallback elsewhere.

**Architecture:** Add a simulator-free backend-name selector to the pharmacy
scene module. `_ensure_genesis()` queries PyTorch CUDA availability lazily,
maps the selector result to a Genesis backend object, and retains its existing
foreign-initialization refusal.

**Tech Stack:** Python 3.13, PyTorch 2.13.0+cu130, Genesis 1.2.3, pytest, dora.

## Global Constraints

- The owner explicitly authorizes this Class C frozen-scene change.
- Do not modify dependencies, the NVIDIA driver, system CUDA, graphs, verifier, or reset.
- Preserve macOS Metal behavior and locked Linux CPU compatibility.
- Never silently retry on CPU after a CUDA initialization failure.
- Use `uv run --no-sync` so the local CUDA wheel is not replaced.

---

### Task 1: Pure backend selection

**Files:**
- Modify: `tests/unit/test_scene_cfg.py`
- Modify: `src/aisle/scenes/pharmacy.py`

**Interfaces:**
- Produces: `select_genesis_backend(platform_name: str, cuda_available: bool) -> str`
- Consumed by: `_ensure_genesis()`

- [ ] **Step 1: Add failing table-driven unit coverage**

Add to `tests/unit/test_scene_cfg.py`:

```python
@pytest.mark.parametrize(
    ("platform_name", "cuda_available", "expected"),
    [
        ("Darwin", False, "metal"),
        ("Darwin", True, "metal"),
        ("Linux", True, "cuda"),
        ("Linux", False, "cpu"),
        ("Windows", True, "cuda"),
        ("Windows", False, "cpu"),
    ],
)
def test_select_genesis_backend(platform_name, cuda_available, expected):
    """SCN-7, CON-5: backend selection is deterministic from platform and
    resolved CUDA availability; macOS remains Metal."""
    from aisle.scenes.pharmacy import select_genesis_backend

    assert select_genesis_backend(platform_name, cuda_available) == expected
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest \
  tests/unit/test_scene_cfg.py::test_select_genesis_backend -q
```

Expected: FAIL because `select_genesis_backend` does not exist.

- [ ] **Step 3: Add the minimal pure selector**

Add near the scene constants in `src/aisle/scenes/pharmacy.py`:

```python
def select_genesis_backend(platform_name: str, cuda_available: bool) -> str:
    if platform_name == "Darwin":
        return "metal"
    return "cuda" if cuda_available else "cpu"
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Step 2 command.

Expected: six parameter cases pass.

### Task 2: Wire Genesis initialization to the selector

**Files:**
- Modify: `src/aisle/scenes/pharmacy.py`
- Modify: `docs/determinism.md`
- Test: `tests/unit/test_scene_cfg.py`
- Test: `tests/sim/test_scene.py`

**Interfaces:**
- Consumes: `select_genesis_backend(platform_name, cuda_available)`
- Produces: Genesis initialized with `gs.metal`, `gs.cuda`, or `gs.cpu`

- [ ] **Step 1: Update `_ensure_genesis()`**

Replace the fixed platform expression with lazy CUDA detection:

```python
system = platform.system()
cuda_available = False
if system != "Darwin":
    import torch

    cuda_available = torch.cuda.is_available()
backend_name = select_genesis_backend(system, cuda_available)
expected = {"metal": gs.metal, "cuda": gs.cuda, "cpu": gs.cpu}[backend_name]
```

Keep `gs.init(...)` and the existing initialized-backend mismatch check
unchanged so CUDA initialization errors propagate.

- [ ] **Step 2: Document the active CUDA backend**

Update `docs/determinism.md` to state that Linux x86-64 was exercised on an
RTX 5090 with driver 580.126.09, that backend selection is automatic, and that
post-step cross-backend identity remains unclaimed.

- [ ] **Step 3: Run focused and full unit verification**

Run:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest tests/unit/test_scene_cfg.py -q
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -m unit
```

Expected: no failures.

- [ ] **Step 4: Run the focused simulator scene test**

Run:

```bash
env -u PYTHONPATH uv run --no-sync pytest \
  tests/sim/test_scene.py::test_build_determinism -q
```

Expected: PASS with Genesis initialized on CUDA.

### Task 3: End-to-end GPU acceptance

**Files:**
- Create: `runs/linux-cuda-seed0-20260801/` through the rollout harness

**Interfaces:**
- Consumes: CUDA-enabled Genesis scene
- Produces: successful T0 episode, traces, video, and observed GPU allocation

- [ ] **Step 1: Start the T0 rollout**

Run:

```bash
env -u PYTHONPATH uv run --no-sync harness rollout \
  --graph graphs/expert_t0.yaml \
  --tier T0 \
  --episodes 1 \
  --seeds 0..0 \
  --no-idea-gate \
  --env-baseline local \
  --timeout-s 900 \
  --run-id linux-cuda-seed0-20260801
```

- [ ] **Step 2: Sample GPU allocation while the rollout is live**

Run:

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory \
  --format=csv,noheader
```

Expected: the AISLE `.venv/bin/python3` process has nonzero GPU memory.

- [ ] **Step 3: Verify rollout result and teardown**

Expected rollout JSON: `"ok": true`, `"pass1": 1.0`, and trace/video paths.

Run:

```bash
ps -eo pid,args | rg 'dora run|dora_genesis.py' || true
git diff --check
```

Expected: no simulator processes remain and the diff has no whitespace errors.

