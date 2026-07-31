# Feature: dataflow-scoped env injection for `dora start` (CLI env does not reach daemon-spawned nodes)

**Filed as [dora-rs/dora#2919](https://github.com/dora-rs/dora/issues/2919) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4; daemon from git rev `7eb4a5f8b`.

## Problem

With `dora run`, nodes are children of the CLI process and inherit its environment, so
`MYVAR=x dora run graph.yaml` configures nodes. With `dora start`, nodes are spawned by
the daemon and inherit the DAEMON's environment — the CLI invocation's env silently does
not apply. The only channel is a per-node `env:` block in the graph YAML, which forces
callers to rewrite/copy graphs just to parameterize a run (seeds, output paths, etc.),
and makes `dora run` -> `dora start` migration subtly breaking.

## Ask

`dora start graph.yaml --env KEY=VAL [--env ...]` applying to every node of that dataflow
(node-level `env:` still wins). Alternatively/additionally: document loudly that CLI env
does not propagate under `start`.
