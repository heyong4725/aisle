# Troubleshooting

The known failure modes, most common first. Every one of these has cost
this project real debugging time.

## Sim imports suddenly fail (`genesis`/`dora` not found)

You ran plain `uv sync`, which REMOVES the sim extras. Re-run:

```bash
uv sync --extra sim
```

Anything that touches the simulator needs the extra; plain sync is only
for pure-unit work.

## Leaked simulator processes (the first thing to check)

A timeout-killed or crashed `dora run` leaves orphaned node processes
behind — historically at high CPU, silently corrupting every
measurement that follows (slow episodes, flaky timing tests, thermal
throttling). Upstream issue: dora-rs/dora#2856.

Before debugging ANY perf/timing weirdness:

```bash
uptime                          # load average sane for an idle box?
ps aux | grep -E "dora|genesis" | grep -v grep
```

Kill leftovers by their run working directory rather than pattern-
matching all of python (other work may be running):

```bash
# inspect first, then kill the pids whose cwd is the stale run
lsof -a -d cwd -c python | grep runs/
```

The campaign runners sweep their own worktrees between sessions; manual
`dora run` invocations are on you.

## `dora --version` mismatch warning

The CLI and Python API must both use release 1.0.1. Check `dora --version`
and `uv run --extra sim python -c "from importlib.metadata import version; print(version('dora-rs'))"`.

- `which dora` identifies older Cargo, conda, or Homebrew binaries that
  may shadow the uv-installed CLI.
- Reinstall with `uv tool install dora-rs-cli==1.0.1` and
  `uv sync --extra sim --locked`.

Stop and restart any older daemon/coordinator before launching 1.0.1 nodes.
The 1.0 release changes binary encoding; release candidates cannot be mixed
with stable nodes. Existing coordinator stores and recordings may also need
migration; consult the [upstream 1.0 release notes](https://github.com/dora-rs/dora/blob/v1.0.1/Changelog.md#v100-2026-09-02)
before reusing them.

## Rollout refuses to start

The refusal JSON says why; the common ones:

- **Frozen-set drift** — your working tree differs from the trusted
  env baseline. If you intentionally changed frozen code, that is an
  `env-change` PR (CON-7), not an override. For local dev on top of a
  known-good tree, `--env-baseline local` (recorded in the manifest).
- **No open idea** (HAR-8) — research-agent branches must
  `harness report log` an idea before rolling out. Humans:
  `--no-idea-gate` (recorded).
- **Validation errors** — fix the graph; the error's `hint` field
  usually names the registry node or adapter you need.
  `INSTALL_MISSING` means the manifest's package is not in the frozen
  environment — pick an installed alternative (see `analysis/h1/` for
  why this matters).

## Episodes hang or time out

- S-tier (retail) episodes are long-horizon; give rollouts a real
  `--timeout-s` and expect minutes per episode, not seconds.
- A graph bug can leave an episode with no termination condition —
  the episode then runs until the outer timeout. If a rollout stalls,
  check whether traces are still being written (`ls -lt
  runs/<id>/traces/`) before assuming the runner is dead.
- One machine, one sim run. Parallel sim runs (or a parallel `uv sync`
  / cargo build during a run) contend for the GPU/CPU and corrupt
  timing.

## Nondeterminism (same seed, different result)

Determinism is a spec requirement (CON-5) with a dedicated writeup:
`determinism.md`. Short version: RNG and time are injected, the scene
is rebuilt from (seed, embodiment, tier), and known nondeterminism
sources (thread pools, unordered dict iteration, wall-clock in result
paths) are bugs — file them as such. The M0 gate includes a
same-seeds replicate check.

## CI red but local green

- Did you run the full gate chain with `&&`? A `;`-chained run can
  scroll a red step past you.
- `tools/trace_check.py` fails when an implemented MUST id has no
  citing test — grep the spec id, add the test docstring citation.
- CI has no simulator; `-m unit` must not import sim deps at
  collection time.
