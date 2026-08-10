# AISLE GPU simulation demo

## Purpose

This demo presents the central AISLE idea: a robot behavior is composed as a
typed dora dataflow, executed in Genesis physics, evaluated automatically, and
recorded as reproducible evidence.

The T0 pharmacy-pick task is the clearest demonstration. A simulated Franka arm
must locate a known medicine box, pick it from the shelf, place it in the tray,
and receive an oracle-verifier verdict. The run exercises the complete path:

```text
typed graph validation
        ↓
dora dataflow launch
        ↓
Genesis scene and robot control
        ↓
automatic reset and verification
        ↓
result JSON, Arrow traces, timing, and video
```

This is more than a robot-animation demo. It shows the infrastructure needed
for an agent to propose, validate, run, measure, and improve robot behaviors
without bypassing the safety or evaluation boundaries.

## What the harness achieves

The AISLE harness currently provides:

- Typed graph validation before launch, including schema compatibility,
  embodiment matching, motion gating, oracle isolation, and dependency checks.
- Deterministic scene construction from a recorded seed and environment hash.
- Automated rollouts over one or more episodes and seeds.
- Automatic reset, episode termination, and oracle verification.
- Structured failure classification instead of a visual-only judgment.
- JSON summaries containing pass@1, pass@8, failures, episode results, and
  wall/simulation durations.
- Arrow traces and overhead video for inspection and later analysis.
- Frozen-environment integrity checks that refuse runs after unrecorded scene,
  verifier, reset, or safety changes.
- An idea gate and experiment records for research-agent campaigns.
- Validated live node replacement and probing through `harness swap` and
  `harness probe`.

Project results include a signed-off M0 pharmacy-pick baseline at 0.98 pass@1
over 50 seeds, a successful H2 iteration result, measured H3 and H4 campaigns,
and implemented retail S1–S3 scenes. See the root `README.md` and `analysis/`
for the formal findings and their limitations.

## Verified status on this workstation

This Linux workstation has been configured locally with:

- PyTorch 2.13.0+cu130
- NVIDIA GeForce RTX 5090
- NVIDIA driver 580.126.09
- Genesis 1.2.3
- dora 1.0.0-rc.4

AISLE selects Genesis CUDA only for an explicit, attested `cuda` rollout. The
verified T0 GPU rollout:

- allocated approximately 1,761 MiB of GPU memory;
- completed with pass@1 1.0;
- took 56.3 seconds wall time for 27.2 simulated seconds;
- produced Arrow traces and an overhead video.

The comparable earlier CPU success took 104.6 seconds wall time. This is a
single-run development comparison, not a formal performance benchmark.

Verification completed on this checkout:

- 629 unit tests passed and 1 skipped;
- 28 simulator/graph tests passed and 2 skipped;
- formatting, lint, traceability, and frozen-environment hash gates passed.

Install the committed CUDA environment before the demo. This is a locked,
mutually exclusive alternative to the portable `sim` extra:

```bash
uv sync --extra cuda
```

## Recommended live presentation

Run all commands from the repository root:

```bash
cd /home/demo/Public/github_aisle/aisle-latest
```

### 1. Validate the typed robot graph

```bash
env -u PYTHONPATH \
  uv run harness validate graphs/expert_t0.yaml
```

Expected result: JSON with `"ok": true`, no validation errors, and no
warnings. This demonstrates that the graph is checked before any simulator
process starts.

### 2. Show the selected GPU

```bash
env -u PYTHONPATH \
  uv run python -c \
  "import torch; print(torch.__version__, torch.cuda.get_device_name(0))"
```

Expected output includes:

```text
2.13.0+cu130 NVIDIA GeForce RTX 5090
```

Optionally monitor the device in another terminal while the demo runs:

```bash
watch -n 1 nvidia-smi
```

### 3. Run the simulation with an interactive viewer

```bash
env -u PYTHONPATH AISLE_HEADLESS=0 \
  uv run harness rollout \
  --graph graphs/expert_t0.yaml \
  --tier T0 \
  --sim-extra cuda \
  --episodes 1 \
  --seeds 0..0 \
  --no-idea-gate \
  --env-baseline local \
  --timeout-s 900
```

The Genesis window should show the Franka arm picking the requested medicine
box and placing it in the tray. The terminal returns one JSON object with the
verdict and artifact paths.

`--no-idea-gate` and `--env-baseline local` are recorded human-development
overrides. Research-agent experiments do not use these shortcuts.

## Reliable recorded demo

For a presentation where repeatability matters more than a live viewer, run
headlessly over three seeds:

```bash
env -u PYTHONPATH \
  uv run harness rollout \
  --graph graphs/expert_t0.yaml \
  --tier T0 \
  --sim-extra cuda \
  --episodes 3 \
  --seeds 0..2 \
  --no-idea-gate \
  --env-baseline local \
  --timeout-s 900
```

Results are written under:

```text
runs/<run-id>/
├── manifest.json
└── traces/
    ├── overhead.mp4
    └── ...
```

The already verified single-seed GPU recording is:

```text
runs/linux-cuda-seed0-r2-20260801/traces/overhead.mp4
```

## What to point out during the demo

1. The YAML graph describes cooperating nodes rather than one monolithic robot
   script.
2. Validation occurs before launch and rejects unsafe or incompatible graphs.
3. Every motion command passes through the budget guard.
4. The verifier—not the policy—has access to privileged oracle state.
5. The run produces machine-readable evidence, traces, video, and timing.
6. The same harness supports agent-driven iteration, held-out evaluation,
   failure analysis, and live node replacement.

## Troubleshooting

If pytest or a harness command imports ROS Humble's Python 3.10 packages, keep
the `env -u PYTHONPATH` prefix.

If PyTorch reports `+cpu`, restore the committed CUDA selection with
`uv sync --extra cuda`. If `--sim-extra cuda` reports that CUDA is unavailable,
stop rather than changing the system NVIDIA driver or allowing a CPU fallback.

If a rollout is interrupted, inspect for stale simulator processes before
starting another:

```bash
ps -eo pid,etimes,pcpu,pmem,args |
  rg 'dora run|dora_genesis.py'
```

Run one simulator workload at a time. Parallel simulator runs or dependency
operations can distort timing and leave stale processes after interruption.
