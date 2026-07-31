# Bug: a failing dynamic node's error is delivered into ANOTHER node's `Node()` init (cross-talk between concurrent dynamic-node initializations)

**Filed as [dora-rs/dora#2917](https://github.com/dora-rs/dora/issues/2917) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4; daemon + Python node API from git rev `7eb4a5f8b`. macOS arm64.

## What happens

Two dynamic nodes added in quick succession: node A (`dora node add`, a Python node whose
init takes a couple of seconds — heavy imports), then ~120 ms later node B (a short-lived
probe that crashed at spawn, before registering). Node B's failure was delivered as the
error result of node A's `Node()` initialization:

```
(node A stderr)
  File ".../grasp_topdown.py", line 216, in main
    node = Node()
RuntimeError: Could not initiate node from environment variable. For dynamic node, please add a node id in the initialization function.
Caused by:
   0: internal error
   1: failed to init event stream
   2: subscribe failed: Node probe-e9bcdc2f exited before initializing dora. ...
Location:
    apis/python/node/src/lib.rs:665:39
```

`probe-e9bcdc2f` is node B — a different node entirely. Node A then exited (ExitCode(1)),
so one crashing dynamic node took down an unrelated healthy one that happened to be
mid-init.

Daemon timeline:

```
18:46:01.317 INFO  adding node ...                node_id=grasp-planner-topdown
18:46:01.319 INFO  node added successfully        node_id=grasp-planner-topdown
18:46:01.442 INFO  adding node ...                node_id=probe-e9bcdc2f
18:46:01.444 INFO  node added successfully        node_id=probe-e9bcdc2f
18:46:01.502 ERROR node exited with error: ExitCode(1)  node_id=probe-e9bcdc2f
18:46:01.518 ERROR node exited with error: ExitCode(1)  node_id=grasp-planner-topdown
```

## Repro

1. Live dataflow; `dora node add` a Python node with slow imports (so its `Node()` call
   is still pending), then immediately `dora node add` a second node that exits before
   registering (e.g., a script that crashes at import).
2. The first node's `Node()` raises with the SECOND node's failure in the cause chain.

**Workaround we ship:** sequence dynamic adds — wait for the earlier node's init to finish
before adding another. With sequencing, both nodes work.

## Ask

Scope daemon init/subscribe replies per requesting node so one dynamic node's failure can
never be delivered as the outcome of another node's initialization.
