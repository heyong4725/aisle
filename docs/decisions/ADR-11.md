# ADR-11: T09 rollout-runner interpretations (SPEC 070)

Status: ACCEPTED

Interpretations chosen (CON-15): (1) The trace recorder (HAR-4) runs in
the rollout runner's INSTRUMENTED copy of the graph and subscribes every
traceable topic including oracle_state; VAL-6's oracle isolation governs
the COMPOSED graph that the HAR-2 gate validates (the original file),
not the harness's own measurement — the instrumented copy exists only
inside runs/<id>/ and is never registered or validated. (2) pass@8
(HAR-3) reads a per-episode `retries` count from the episode records;
the task-state-machine is single-attempt at M0 (ADR-10), so retries is
0 and pass8 == pass1 — computed per-episode either way, NEVER best-of-8
independent episodes. (3) Traces are one Arrow IPC file per topic with
columns (sim_time_ns, env_id, seq, data list<float64>); rgb_overhead
becomes overhead.mp4 at 10 fps instead of an Arrow column (a raw frame
table would dwarf every other artifact). Image topics other than the
overhead camera are not traced at M0. (4) HAR-6's --episode/--node
filters are deferred: the M0 query slices by topic and sim-time range
and summarizes rate/extremes/gaps; episode windows can be recovered from
reset_done rows. (5) The rollout wall timeout defaults to 420 s (one
genesis build) + 150 s per episode; runs that produce fewer episodes
than requested return ok=false with whatever was recorded (results are
flushed per line). (6) Seeds cycle to fill --episodes when the seed
range is shorter. (7) The 50-episode M0-1 gate test is authored per the
spec's acceptance list but skip-marked pending the under-board coverage
decision (ADR-10 section 8) — it is the T10 gate, and pretending T09
must pass it would be dishonest. (8) `--no-idea-gate` (humans only) is
recorded in the run manifest (HAR-2 "flag is logged").

## Amendments from the T09 live bring-up

(9) Front-mode grasp plans REFUSE when the wrist flip exceeds
CONTINUITY_MAX: no multi-radian flip has ever executed stably (a ~2.5
rad planned flip diverged to 3.4 rad of tracking error and wrapped the
arm into a physics NaN that CRASHED the bridge, killing the whole run).
Refused plans leave the arm idle and the episode closes honestly via
the verifier timeout (never_grasped) — front-mode coverage remains the
ADR-10 section 8 owner decision. (10) The runner detects stalls: a dead
bridge leaves `dora run` alive with the trace stream frozen, so the run
bails 180 s after trace growth stops (600 s grace before the FIRST data
— the genesis build produces no traces). (11) `dora run` executes with
cwd = the run directory so node cwds match the orphan reaper's filter
(with cwd = repo root the filter matched nothing and leaked nodes raced
cleanup); the reaper SIGTERMs before SIGKILL so the trace recorder can
flush. (12) Traces use Arrow IPC STREAM format read batch-defensively:
the FILE format needs a close-time footer and a killed recorder left
unreadable files. (13) tools/env_hash.json is committed NOW (pre-M0)
and ci.sh runs --check: the HAR-2 gate needs a committed hash, and any
PR touching frozen files must regenerate it (CON-7's commit-at-M0 is
treated as a deadline, not a start date). (14) Clause (3) is
AMENDED for the realistic verifier: with `AISLE_FRAME_CAPTURE_PERIOD_S`
set, the recorder ALSO persists raw pixels as
`frames/<camera>/<sim_time_ns>.npz` — one file per camera per judged
instant, holding exactly the `{rgb, depth}` mapping
`verifier.realistic.judge_frames` consumes. Clause (3)'s reasoning still
holds for a per-FRAME table (30 Hz of 480x640 RGB would dwarf every
other artifact), but the mp4 it chose is lossy, 10 fps and carries NO
depth, so a run recorded under (3) alone cannot be replayed through the
verifier at all: VER-6's offline comparison and VER-7's byte-equality
both need the arrays the live judge saw. Capture is therefore opt-in and
cadenced rather than continuous — the VER-9 judged-frame set (period
checkpoints plus the terminal frame, forced when `episode_result`
arrives) at 140 KB per instant measured live, ~17 MB for a 600 s episode
at 5 s. The selector matches `checkpoint_stamps` in both respects that
decide WHICH frame is persisted: each checkpoint takes the last frame AT
OR BEFORE the boundary (a frame after it is a different frame, and VER-7
compares bytes), and the schedule is re-based on each `episode_goal`
stamp rather than running process-global, since VER-9 counts checkpoints
from goal receipt (PR #105 review). The
overhead rgb/depth pair is written only when both come from ONE render
(BRG-2 co-renders them when both are due): the geometry stages fuse the
two, so a mismatched pair would measure a scene that never existed, and
a gap is the safe outcome. Issue #136 closes the rate-asymmetry hole in
that rule: 30 Hz RGB can advance between 15 Hz RGB/depth pairs, so recorder
and live verifier retain the last complete pair separately from per-stream
latest values. A second-half-phase checkpoint therefore selects the complete
pair before its boundary rather than waiting for a complete pair after it.
