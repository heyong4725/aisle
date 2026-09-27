# Simulation backends

AISLE can realize the same scene and bridge contract with three physics
engines. Select one with `harness rollout --sim-engine`; the harness injects
the choice into the bridge and records it in the run manifest.

| Engine | Physics | Renderer | Installation | Status |
|---|---|---|---|---|
| `genesis` | Genesis | Genesis | Included in the `sim` or `cuda` extra | Default; only engine behind the measured record |
| `nexus` | Nexus GPU | Nexus | Build the pinned `nexus3d` wheel | Development only |
| `rapier` | Rapier CPU | Nexus | Build both `nexus3d` and `rapier3d` | Development only |

Results from different engines are not directly comparable. Each run records
the engine, resolved backend and device, the engine realization digest, and
the optional wheel build receipt.

## Genesis

Genesis is installed with the normal simulation environment:

```bash
uv sync --extra sim --locked
```

It is the default when `--sim-engine` is omitted. An explicit development
rollout is:

```bash
uv run --extra sim --locked harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local \
    --sim-engine genesis
```

On NVIDIA Linux, use `uv sync --extra cuda --locked`, use `--extra cuda` on
the `uv run`, and add `--sim-extra cuda`. The CUDA request fails closed if no
CUDA device is available.

## Nexus

Nexus is outside `uv.lock`. The installer fetches the source commit pinned in
`engine-runtime.json`, builds the wheel with maturin, installs it into the
project environment, and writes `.nexus-runtime-receipt.json`.

Start from the normal `sim` environment, then install and verify Nexus:

```bash
uv sync --extra sim --locked
uv run --no-sync python tools/nexus_runtime.py install
uv run --no-sync python tools/nexus_runtime.py verify
```

Run a graph with Nexus:

```bash
uv run --no-sync harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local \
    --sim-engine nexus
```

The default Nexus build feature is `metal` on macOS and `webgpu` elsewhere.
Use `tools/nexus_runtime.py install --feature <feature>` when the wheel needs a
different compiled feature. `AISLE_SIM_BACKEND` accepts `metal`, `webgpu`,
`cuda`, or `cpu`, but ordinary rollouts should let the harness resolve and
attest the backend from `--sim-extra` and the host.

To build a local Nexus checkout instead of the pinned source:

```bash
uv run --no-sync python tools/nexus_runtime.py install --nexus ../nexus
```

## Rapier

Rapier steps physics on the CPU and uses Nexus for rendering. Install Nexus
first, then build and verify the pinned Rapier Python bindings:

```bash
uv sync --extra sim --locked
uv run --no-sync python tools/nexus_runtime.py install
uv run --no-sync python tools/rapier_runtime.py install
uv run --no-sync python tools/rapier_runtime.py verify
```

Run a graph with Rapier:

```bash
uv run --no-sync harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local \
    --sim-engine rapier
```

`tools/rapier_runtime.py install --rapier ../rapier` builds a local checkout.
Add `--determinism` to build Rapier's enhanced determinism feature when the
experiment requires cross-platform math consistency.

## Selection rules

- `--sim-engine {genesis,nexus,rapier}` is available on `harness rollout`,
  `harness fleet`, `harness monolith run`, `harness fault calibrate`, and
  `harness skill register`.
- A graph can declare `AISLE_SIM_ENGINE` in its simulation bridge node. A
  conflicting CLI selection is refused instead of silently overriding the
  graph.
- For a direct `dora run`, set `AISLE_SIM_ENGINE` in the bridge node's `env`
  mapping. Prefer the harness for recorded work because it validates, injects,
  hashes, and records the engine choice.
- Nexus and Rapier require clean build receipts. A missing, malformed, or dirty
  source receipt fails the environment gate before launch.

## Keep optional wheels installed

Plain `uv sync`, and `uv run` without `--no-sync`, restore the locked
environment and remove the out-of-lock Nexus and Rapier wheels. After either
optional installer runs, use `uv run --no-sync ...` for verification, tests,
and rollouts. If a command reports that an optional engine is not installed,
rerun its installer and verifier.

More detail:

- [Getting started](getting-started.md), including platform prerequisites
- [Harness CLI guide](harness-guide.md)
- [Troubleshooting](troubleshooting.md)
- [ADR-67: Nexus engine](decisions/ADR-67.md)
- [ADR-68: Rapier engine](decisions/ADR-68.md)
