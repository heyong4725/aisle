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

## Historical workstation evidence

Before the locked, attested CUDA path landed, a contributor workstation was
configured locally with:

- PyTorch 2.13.0+cu130
- NVIDIA GeForce RTX 5090
- NVIDIA driver 580.126.09
- Genesis 1.2.3
- dora 1.0.0-rc.4

That workstation produced a T0 GPU development rollout which:

- allocated approximately 1,761 MiB of GPU memory;
- completed with pass@1 1.0;
- took 56.3 seconds wall time for 27.2 simulated seconds;
- produced Arrow traces and an overhead video.

The comparable earlier CPU success took 104.6 seconds wall time. Both numbers
are historical, single-run development measurements, not a formal performance
benchmark. The GPU run predated the committed `cuda` extra and therefore does
not attest the current dependency/backend path. Current test totals and lock
resolution evidence belong in the PR/CI record for the exact commit under
review, rather than as a count in this long-lived guide.

AISLE now selects Genesis CUDA only for an explicit, attested `cuda` rollout.
The corrected path is covered by fail-closed identity/manifest tests and locked
Linux CPU/CUDA resolution checks; a fresh hardware run is still required before
claiming attested performance for it.

Install the committed CUDA environment before the demo. This is a locked,
mutually exclusive alternative to the portable `sim` extra:

```bash
uv sync --extra cuda
```

## Recommended live presentation

Run all commands from the repository root:

```bash
cd aisle
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

The historical single-seed GPU recording was stored at the following
contributor-local path:

```text
runs/linux-cuda-seed0-r2-20260801/traces/overhead.mp4
```

`runs/` is gitignored, so that file is not distributed with the repository and
must not be presented as reviewable evidence for the current head. Generate a
new recording with the command above for a live demonstration.

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


## Recorded clips (illustrations, not evidence)

Every clip below is cut deterministically from a committed run's
`traces/overhead.mp4` by `tools/paper_media.py`; the manifest
(`docs/media/manifest.json`) names each clip's run id, seeds, sim
window, and speed factor. **The run record is the evidence; the clip
is the illustration** — this repo's founding complaint about demo
culture applies to its own videos.

| Clip | What it shows | Record |
|---|---|---|
| [T1 expert pick](media/t1_expert_pick.mp4) (2x) | the named-med baseline: locate, grasp, deliver, verified success | `hybrid-t1-noregression-r2`, seed 30 |
| [T2 label-read pick](media/t2_label_read_pick.mp4) (3x) | the registered stack on the label tier: read tour, staged approach, grasp | `t2-scope-v2`, seed 12 |
| [T4 recovery chain](media/t4_recovery_chain.mp4) (4x) | scripted misdelivery → "take it back" → return-to-shelf → correct redelivery, both goals verified | `t4-inc2-recovery-r4`, seed 0 |
| [M1 lockstep policy](media/m1_lockstep_grasp_drop.mp4) (4x) | the fine-tuned VLA's only recorded pick behavior — grasp, then drop (scored `dropped`; the honest 0/8's most interesting episode) | `m1-lockstep-n8`, seed 32 |

GitHub renders these inline; locally any player opens them.
