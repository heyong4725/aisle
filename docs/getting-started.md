# Getting started

Primary platform: macOS arm64 (M-series). Linux runs the non-sim suite and,
with the explicit `cuda` extra, the Genesis simulator on supported NVIDIA
hardware. The signed-off M0 experiments ran on an M3 MacBook; the Linux CUDA
measurements described in `docs/demo.md` are development evidence, not a
cross-backend reproducibility claim.
Python >= 3.11, managed exclusively through
[uv](https://docs.astral.sh/uv/) — never bare pip/conda (CON-2).

## 1. Install

```bash
git clone https://github.com/heyong4725/aisle && cd aisle
uv sync --extra sim
```

The `sim` extra pulls Genesis, torch, and the dora python API. Two
things to know before anything else:

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
- **dora runs from source, not PyPI.** PyPI's latest dora wheel (0.5.0)
  is far behind dora main and lacks the dynamic-node command family this
  repo depends on (`dora node add/remove`, hot-swap). The python API
  builds from git at the rev pinned in `pyproject.toml`
  `[tool.uv.sources]`.

The dora **CLI** must be cargo-installed from the SAME rev as the
python API (needs a Rust toolchain — `rustup` if you don't have one):

```bash
cargo install --git https://github.com/dora-rs/dora --rev cd597e705 dora-cli --locked
dora --version    # prints a warning if the CLI/python-API revs drift
```

If you see a version-mismatch warning, check which `dora` binary is on
your PATH (`which dora`) — stray conda environments are the usual
culprit.

## 2. Verify the install

```bash
uv run pytest -m unit          # no simulator, about 90 seconds
uv run pytest -m "sim or graph"   # brings up Genesis; several minutes
```

Every harness CLI prints JSON to stdout, logs to stderr, and exits 0
iff ok (CON-8) — pipe anything to `jq`.

```bash
uv run harness validate graphs/expert_t0.yaml
```

## 3. Run the expert graph

The hand-written T0 baseline (pick a known box into the tray) is the
repo's integration test and your first end-to-end run:

```bash
uv run harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local
```

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
