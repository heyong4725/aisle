# Feature: dataflow completion semantics for timer-driven graphs + process-group cleanup when the CLI is killed

**Filed as [dora-rs/dora#2920](https://github.com/dora-rs/dora/issues/2920) (2026-07-31). RESOLVED at our pin `cd597e705` (PR #85):**
- **Problem 2 (orphan leak) is fixed** — #2949 (graceful SIGTERM teardown) + #3018 (nodes ended when the CLI is killed outright). Retest at the pin (2026-08-05): SIGTERM and SIGKILL of a live `dora run` both left **zero** surviving node processes. The harness reaper stays as a belt only (a slow node can still outlive the graceful-stop window).
- **Problem 1 (completion) shipped as `dora run --exit-when-nodes-finish`** (#2957): timers count as clocks, a node finishes when its DATA inputs close. **AISLE cannot adopt it for control graphs**: the guard→bridge→expert(/driver)→guard command loop is a data-input cycle, so closure never propagates through it — the harness keeps managing shutdown itself (results-file watcher + kill), which is now leak-free. The flag works for acyclic pipelines whose sources terminate.

**Versions:** dora-cli 1.0.0-rc.4; daemon + API from git rev `7eb4a5f8b`. macOS arm64.

## Problem 1: a timer keeps the dataflow alive forever

A graph whose worker nodes all finish cleanly never exits if any node consumes a
`dora/timer/...` input: the timer keeps ticking and `dora run` blocks indefinitely.
Observed: our client node logged "all N episodes done" and exited successfully; the
dataflow then sat for 9+ minutes (until an external kill), with the sim node discarding
timer events the whole time. (Verified with `dora run`, headless and windowed; under
`dora start` staying alive until `dora stop` is presumably intended, so this ask is
about `run`'s batch-style use.)

## Problem 2: killing `dora run` leaks every node process

When the blocked `dora run` is killed externally (`timeout N dora run ...`, Ctrl-C storm,
CI reaper), the node processes survive as orphans. We have measured leaked nodes at high
CPU skewing later timing-sensitive runs three separate times; each ad-hoc smoke needs a
manual `pkill` sweep afterwards.

## Asks

- An opt-in completion policy: e.g. `dora run/start --exit-when-nodes-finish` (timers
  don't count as keep-alive), or `--exit-on <node-id>` (dataflow ends when the named node
  exits). Either makes "run N episodes and exit" scriptable without wrapping in timeouts.
- `dora run` should place nodes in its own process group / kill children on abnormal
  exit, so an external kill of the CLI cannot leak simulator processes.
