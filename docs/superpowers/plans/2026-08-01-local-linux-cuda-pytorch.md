# Local Linux CUDA PyTorch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CPU-only PyTorch in this checkout's `.venv` with the official
PyTorch 2.13.0 CUDA 13.0 build and verify AISLE on the RTX 5090.

**Architecture:** Keep repository dependency metadata unchanged to comply with
CON-1. Apply a workstation-local environment override through `uv pip`, relying
on the existing NVIDIA driver and installing only project-local wheel contents.

**Tech Stack:** uv 0.11, Python 3.13, PyTorch 2.13.0+cu130, CUDA 13.0 wheel
index, Genesis 1.2.3, dora 1.0.0-rc.4.

## Global Constraints

- Do not invoke an OS package manager, NVIDIA installer, or CUDA toolkit installer.
- Do not modify the NVIDIA driver, `/usr/local/cuda`, `pyproject.toml`, or `uv.lock`.
- Install only into `/home/demo/Public/github_aisle/aisle-latest/.venv`.
- Stop without changing the host if the official wheel cannot use the existing driver.
- Isolate ROS Humble's Python 3.10 paths during AISLE verification.

---

### Task 1: Replace and verify the local PyTorch distribution

**Files:**
- Modify: `.venv/` through `uv pip` package installation
- Verify unchanged: `pyproject.toml`
- Verify unchanged: `uv.lock`

**Interfaces:**
- Consumes: NVIDIA driver 580.126.09 and RTX 5090 device 0
- Produces: local `torch==2.13.0+cu130` with a working CUDA device

- [ ] **Step 1: Record the fail-first CUDA check**

Run:

```bash
uv run python - <<'PY'
import torch
assert torch.version.cuda == "13.0", torch.__version__
assert torch.cuda.is_available()
assert "RTX 5090" in torch.cuda.get_device_name(0)
PY
```

Expected: FAIL because the installed distribution is `torch==2.13.0+cpu`,
`torch.version.cuda` is `None`, and CUDA is unavailable.

- [ ] **Step 2: Confirm the official wheel and host remain compatible**

Run:

```bash
nvidia-smi
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/cu130 \
  'torch==2.13.0+cu130'
```

Expected: the existing driver remains 580.126.09; uv replaces only packages in
`.venv` and does not invoke sudo, apt, dnf, an NVIDIA installer, or a CUDA
toolkit installer.

- [ ] **Step 3: Run the CUDA check and a real tensor operation**

Run:

```bash
env -u PYTHONPATH uv run --no-sync python - <<'PY'
import torch
assert torch.version.cuda == "13.0", torch.__version__
assert torch.cuda.is_available()
assert "RTX 5090" in torch.cuda.get_device_name(0)
x = torch.tensor([20.0, 22.0], device="cuda")
assert x.sum().item() == 42.0
print(torch.__version__, torch.cuda.get_device_name(0), x.device)
PY
```

Expected: PASS and output names PyTorch 2.13.0+cu130, RTX 5090, and `cuda:0`.
`--no-sync` is mandatory because the committed lock selects CPU torch on Linux.

- [ ] **Step 4: Verify graph and unit behavior**

Run:

```bash
env -u PYTHONPATH uv run --no-sync harness validate graphs/expert_t0.yaml
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -m unit
```

Expected: graph JSON has `"ok": true`; unit suite has no failures.

- [ ] **Step 5: Run one GPU-backed T0 rollout**

Run:

```bash
env -u PYTHONPATH uv run --no-sync harness rollout \
  --graph graphs/expert_t0.yaml \
  --tier T0 \
  --episodes 1 \
  --seeds 0..0 \
  --no-idea-gate \
  --env-baseline local \
  --timeout-s 900
```

Expected: JSON has `"ok": true`, `"pass1": 1.0`, trace output, and an overhead
video. During the rollout, `nvidia-smi` must show the AISLE Python process using
device memory or compute.

- [ ] **Step 6: Verify isolation and teardown**

Run:

```bash
git status --short
uv pip show --python .venv/bin/python torch
ps -eo pid,args | rg 'dora run|dora_genesis.py' || true
```

Expected: only the design/plan documents are untracked, torch reports
2.13.0+cu130 in `.venv`, and no dora/Genesis process remains.

