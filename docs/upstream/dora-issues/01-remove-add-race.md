# Bug: `dora node remove` -> `node add` (same id) race — old process's SIGKILL is attributed to the freshly added node, marking it failed

**Filed as [dora-rs/dora#2916](https://github.com/dora-rs/dora/issues/2916) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4; daemon/coordinator + Python node API built from git rev `7eb4a5f8b` (CLI cargo-installed from the same rev). macOS arm64 (Darwin 25.5.0).

## What happens

Hot-swapping a Python node on a live dataflow via `dora node remove -d <df> <id>` immediately followed by `dora node add -d <df> --from-yaml <same-id node>` succeeds at the CLI level, but the daemon then attributes the REMOVED process's kill to the node identity, ~15 ms AFTER the add:

```
18:29:02.565671  INFO  removing node from running dataflow   node_id=grasp-planner-topdown
18:29:02.577049  INFO  adding node to running dataflow       node_id=grasp-planner-topdown
18:29:02.592507  INFO  node added successfully               node_id=grasp-planner-topdown  dynamic=false
18:29:02.592585  WARN  process was killed on drop because it was still running
18:29:02.593590  ERROR node exited with error: Signal(9)     node_id=grasp-planner-topdown
```

From that point the dataflow keeps running but the node is dead/marked failed: in our
pipeline every later episode failed with the planner silent, and `dora stop` reported
`Dataflow <uuid> failed: Node grasp-planner-topdown failed: exited because of signal SIGKILL`
even though the SIGKILL belonged to the OLD instance.

## Repro

1. `dora up`; `dora start graph.yaml --uv --name df --detach` (any graph with a Python
   node `n` consuming a timer works).
2. `dora node remove -d df n && dora node add -d df --from-yaml n.yaml` (back-to-back,
   as any scripted caller does; `n.yaml` = the same node entry).
3. Watch the daemon log: "node added successfully" then "node exited with error: Signal(9)".

**Workaround we ship:** sleep ~2 s between remove and add — with the gap, the old exit is
accounted first and the replacement runs correctly (verified over repeated live swaps).

## Asks

- `node remove` should return only after the removed process is reaped AND its exit is
  accounted — or process accounting should be keyed by process instance/generation,
  not by node id, so a late exit of the old instance cannot poison the new one.
- Bigger picture: an atomic **`dora node replace -d <df> <id> --from-yaml <node>`** is the
  primitive live-iteration workflows actually want (remove+add with no observable gap and
  no identity confusion).
