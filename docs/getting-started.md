# Getting started

Primary platform: macOS arm64 (M-series). Linux supports CPU simulation with
the `sim` extra and GPU simulation on supported NVIDIA hardware with the
explicit `cuda` extra. The signed-off M0 experiments ran on an M3 MacBook; the Linux CUDA
measurements described in `docs/demo.md` are development evidence, not a
cross-backend reproducibility claim.
Install Git and rustup as well as Python >= 3.11, managed exclusively through
[uv](https://docs.astral.sh/uv/) — never bare pip/conda (CON-2).

## 1. Install

```bash
git clone https://github.com/heyong4725/aisle && cd aisle
uv sync --extra sim --locked
```

The `sim` extra pulls Genesis, torch, and the Dora Python API.
Things to know before anything else:

- **Plain `uv sync` REMOVES the sim extras.** If a sim test suddenly
  can't import `genesis` or `dora`, this is why. Re-run with
  `--extra sim`.
- **On an NVIDIA host, use `--extra cuda` instead.** `sim` resolves the
  CPU torch on Linux (CON-1 keeps CUDA wheels out of the default set), so
  Genesis runs on CPU even with a GPU present. `uv sync --extra cuda`
  installs the same stack with the CUDA torch; the two extras are
  mutually exclusive. Pass `--sim-extra cuda` to `harness rollout`; the
  request fails closed if CUDA is unavailable and is recorded in the run
  manifest. The default `--sim-extra sim` never auto-upgrades to CUDA.
- **The Python API remains pinned to 1.0.1; the CLI uses the declared source
  revision.** The release CLI 1.0.1 loses required events under timer pressure
  (#516). Install the corrected source pin rather than relying on version
  output, which is also 1.0.1 for the corrected binary.

On Ubuntu 24.04, install the CPU quickstart's rendering prerequisites. Genesis
constructs an offscreen renderer even when physics runs on CPU:

```bash
sudo apt-get update
sudo apt-get install --yes --no-install-recommends libegl1 libgl1 libgl1-mesa-dri
```

Build and verify the declared runtime in a fresh directory outside the checkout:

```bash
rustup toolchain install 1.97.1
export AISLE_DORA_PREFIX="$PWD/../aisle-dora-runtime"
uv run --extra sim --locked python tools/dora_runtime.py install --prefix "$AISLE_DORA_PREFIX"
uv run --extra sim --locked python tools/dora_runtime.py verify --prefix "$AISLE_DORA_PREFIX"
```

Choose another new prefix if that directory exists. On an NVIDIA setup, use
`--extra cuda` in these uv commands to preserve the selected torch build.
The installer checks the source commit, Cargo.lock, compiler and paired API,
and retains the executable hash in a receipt. See
[the runtime guide](benchmark/v1/dora-runtime.md). A changed pin requires a new
installation; do not edit an old receipt to make it match.

For the supported CPU/Metal quickstart, while the checkout has no existing runs:

```bash
uv run --extra sim --locked python tools/quickstart.py --runtime-prefix "$AISLE_DORA_PREFIX"
```

This executes graph validation, a public task, bundle validation and reporting.
The quickstart uses the `sim` extra; CUDA experiments use the manual rollout
path below. The Linux clone and archive paths passed with verified source
receipts in [run 34082153283](https://github.com/heyong4725/aisle/actions/runs/34082153283).
That evidence does not establish independent reproduction or release readiness.

For manual harness and graph commands, make the verified binary available to
child processes in the current shell:

```bash
export PATH="$AISLE_DORA_PREFIX/bin:$PATH"
```

Stop any older daemon/coordinator you started before switching runtime builds.

## 2. Verify the install

```bash
uv run --extra sim --locked pytest -m unit    # no simulator, several minutes
uv run --extra sim --locked pytest -m "sim or graph"   # brings up Genesis; several minutes
```

Every harness CLI prints JSON to stdout, logs to stderr, and exits 0
iff ok (CON-8) — pipe anything to `jq`.

```bash
uv run --extra sim --locked harness validate graphs/expert_t0.yaml
```

## 3. Run the expert graph

The hand-written T0 baseline (pick a known box into the tray) is the
repo's integration test and your first end-to-end run:

```bash
uv run --extra sim --locked harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local
```

- On NVIDIA hosts, replace `--extra sim` with `--extra cuda` in the
  verification and manual harness commands, and add `--sim-extra cuda` to
  the rollout command.
- `--no-idea-gate` and `--env-baseline local` are the human/dev
  overrides (both recorded in the run manifest). Research agents run
  without them: rollouts then require an open idea-tree entry (HAR-8)
  and a trusted frozen-set baseline (ADR-21).
- Results land in `runs/<run-id>/`: per-episode results JSON, Arrow
  traces, and videos. `runs/` is gitignored; every run is reproducible
  from (graph hash, env hash, seed list) (CON-5).

You can also launch a graph directly, without the rollout wrapper:

```bash
dora run graphs/expert_t0.yaml --uv
```

Sim runs want the machine to themselves — close other GPU/CPU-heavy
work, and see `docs/troubleshooting.md` if runs behave strangely
(leaked simulator processes from a previous killed run are the most
common cause).

## 4. Where to go next

- `docs/physical-ai-primer.md` — the concepts behind the project
  (Physical AI, VLM/VLA/world models/WAMs, sim-to-real, agentic
  auto-research) mapped to where each lives in this repo — start here
  if you are new to the field itself.
- `docs/architecture.md` — what the nodes, graphs, and harness are and
  how they fit together.
- `docs/development-workflow.md` — the spec-driven loop, quality gates,
  and PR conventions (read before your first change).
- `docs/experiments.md` — the hypotheses, what has been measured, and
  where findings live.
- `CLAUDE.md` — the development-agent contract; short, and humans are
  held to it too.
- `docs/Project_AISLE_Experiment_Design.md` — the full design doc
  (the WHY behind everything above).
