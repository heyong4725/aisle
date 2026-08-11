# ADR-h4-iteration-protocol — H4 iteration-latency protocol (hot-swap vs relaunch)

Status: ACCEPTED (CON-15 — interpretation recorded, proceeding).
Date: 2026-07-31. Scope: design doc §8.3 item 5, §9.1 decision 1,
hypothesis §6 H4; SPEC 070 HAR-10/HAR-12.

## Question

H4's measurable core: how long from "the agent decides to change a
node" to "the first episode result produced under the changed graph",
for the two iteration mechanisms the substrate offers — RELAUNCH
(stop the dataflow, edit, validate, start again) vs HOT-SWAP (mutate
the live dataflow via HAR-10). HAR-12 defines the raw metric:
idea-open ts → first episode result after the change.

## Protocol

1. **Context.** Both paths begin from a RUNNING T0 dataflow
   (`expert_t0` derivative, daemon mode) mid-episode-stream with at
   least one completed episode — the realistic iteration context
   (robots executing while the agent thinks; ENPIRE's idle-robot
   problem). Seeds stream 0..29 so the stream never runs dry.
2. **The change.** A NULL-VARIANT replacement of
   `grasp-planner-topdown` (identical node entry): behavior is held
   constant so the measurement isolates MECHANISM latency; a real code
   edit adds the same authoring time to both paths. The relaunch path
   rewrites the graph file through the same document path the swap
   validates, then relaunches; the hot-swap path calls HAR-10 `swap`
   (which validates the post-swap graph internally).
3. **Per repetition.** Log an idea (HAR-7; t_idea) → perform the
   change via the path under test → poll the episode stream → the
   result is the first episode that STARTED at or after the change
   completed (an episode straddling a change is NOT credited — the
   H4 shakeout showed a straddling episode can fail from the swap
   itself, and the relaunch path kills it outright) → latency =
   that episode's result ts − t_idea. Close the idea with the
   measured latency.
4. **Repetitions.** N=6 per path, interleaved R,H,R,H,… on one
   machine, orphan-reap between relaunches. Episode start times are
   derived from the results stream: episode i starts when episode
   i−1's result lands (teleport reset is sub-second at T0; the
   approximation error is well under the effect size).
5. **Evidence.** Per-rep JSONL (path, t_idea, t_change_ok, episode
   timeline, credited episode, latency) + the HAR-12 swap log +
   run manifest (git_sha, env_hash, dora CLI/API pair) under
   `analysis/h4/records/`. The analyzer (`tools/h4_iteration.py
   --analyze`) recomputes latencies from the raw record — the table
   is derived, never hand-written.

## Interpretation bounds

- This measures the SUBSTRATE's iteration overhead, not agent
  authoring time, and not the monolithic-script control condition
  (H4's full comparison needs the script-control campaign; this is
  the dataflow-side half the design doc names the headline plot).
- T0 scene build (~1–2 min) dominates relaunch; S-tier scenes build
  slower, so T0 gives the CONSERVATIVE (smallest) gap.
- Hot-swap latency includes the settle workaround for
  dora-rs/dora#2916 (2 s) and rides on the shakeout-hardened HAR-10
  (health belt, dora#2916/#2917/#2918 workarounds documented in
  `docs/upstream/dora-issues/`).
- The dataflow runs in daemon mode (`dora up`/`dora start`), the only
  mode that supports live mutation; episode-0 outcomes under daemon
  mode showed run-to-run variation during the shakeout, so per-rep
  context (which seed, warm scene) is recorded in the evidence.

## Revision 2 (2026-07-31, PR #79 review)

Rev 1's fixed R,H,R,H order with the idea logged the instant the
stream's first result landed PHASE-LOCKED the measurement: every
hot-swap idea arrived 0.5–7.7 ms after an episode result, so the next
episode was always a straddler and the hot-swap arm always paid the
worst-case wait (confirmed from the rev-1 records). Rev 2 randomizes
BOTH the path order (seeded shuffle of N relaunch + N hot-swap) and
the idea-arrival phase (seeded uniform(0, 25 s) delay after the
stream-health check), with the seed recorded in the batch manifest.
Rev-1 records are retained as superseded evidence and excluded from
the published table. Additionally: batch manifests now record the
full CON-5 tuple with an explicit UNATTESTED label (dev measurement,
ADR-24), episode timelines are sampled by a continuous 4 Hz background
thread (synchronous polling left gaps that mis-credited episodes), and
orphan reaping is scoped to the dataflow's own pids (never a global
pattern kill).
