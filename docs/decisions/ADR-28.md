# ADR-28 — Sim-anchored S1 control loops: retime waypoint-nav and the base watchdog onto base_pose (issue #71)

Status: PROPOSED (agent-drafted 2026-08-11 at the owner's direction — the
CON-7 item issue #71 left open; ratified iff the owner merges the
`env-change` PR carrying it, which IS the CON-7 human review). Relates to
ADR-25 (reset-anchored startup), ADR-26 (CON-5 layering), SPEC 210
MOB-2/MOB-3, SPEC 080, CON-5.

## Problem

ADR-25's residual list (and issue #71's owner pass) left one S1-only
wall-coupling channel with a fixed cause: two control loops tick on WALL
timers —

- `waypoint-nav` recomputed `base_cmd` on `dora/timer/millis/20`;
- `budget-guard` ran its stale/timed-out base watchdog on a dedicated
  `base_watchdog: dora/timer/millis/50` input, with staleness measured
  in WALL seconds (`time.monotonic`).

Command VALUES were already pose-determined, but HOW MANY commands were
computed between two poses, and which sim tick a recompute landed on,
was a wall race (and a function of host rtf). The desk tier has no such
timer-driven control nodes — which is why T0 replicated cleanly while S1
did not. Fixing this required editing `graphs/expert_s1.yaml` and
`budget_guard.py`, both in the CON-7 frozen hash: it waited for an
owner-initiated change, which this ADR records.

## Decision

The S1 control plane is clocked by `base_pose` (50 Hz SIM cadence,
MOB-1; reset-anchored per ADR-25), not by wall timers:

1. **waypoint-nav drives on `base_pose`.** The node's `tick` input is
   removed (graph + manifest). Each pose event updates the machine and
   immediately runs one control iteration: exactly one `base_cmd` per
   serviced pose, so the command SEQUENCE is a function of the pose
   stream alone. `base_pose` is wired with `queue_size: 10` instead of
   latest-wins (see the queue-depth rationale below). The nav machine's
   budgets were already sim-time (PR #21 round 4); feedback now rides
   the 50 Hz sim cadence, comfortably above the MOB-2 2 Hz floor.
2. **The guard watchdog rides `base_pose` and measures staleness in SIM
   seconds.** The dedicated `base_watchdog` timer input is removed
   (graph, manifest, node). On each pose event the guard checks, per the
   pose's `env_id`: a moving base whose last `base_cmd` reference stamp
   is more than `base_staleness_s` SIM seconds behind the pose stamp is
   stopped once ([0, 0] + `base_stale` violation), exactly as before. A
   command's reference stamp is the newest pose stamp seen when it
   arrived (commands do not carry sim stamps; a command latched before
   any pose is anchored at the FIRST pose stamp seen — a 0 reference
   would falsely stale-stop the first command of a guard that joined
   mid-run, since the sim clock is monotonic across episodes, while the
   first-pose anchor still lets a dead producer go stale one
   base_staleness_s later). The BG-2
   episode WALL timeout keeps its injected wall clock and is now
   evaluated at pose arrival — poses flow whenever the sim steps, so
   enforcement is preserved.
3. **A fail-closed WALL net sweeps all envs on the BG-5 stats tick**
   (review findings on the first draft): the sim clock is blind to an
   unstamped pose source, a hung sim (no pose events at all), and an
   env_id that never appears on the pose stream (the old timer loop
   swept every env). A latched moving base with no base_cmd for
   `base_wall_backstop_s` (10 s) is stopped under its own reason
   `base_stale_wall` (the net firing is an ops alarm, distinguishable in
   guard_stats from the sim mechanism working as designed); the BG-2
   episode wall budget is enforced on the same sweep. **The net only
   arms when the sim clock is demonstrably blind** (PR #156 review): the
   latest pose carried no usable stamp, no pose ever arrived for the
   env, or the pose stream itself went silent past the backstop — so a
   healthy-but-slow sim structurally cannot trip it at ANY rtf; while
   valid stamps flow, the sim-time check owns the verdict. The whole
   verdict — timeout > sim-stale > wall-silent — lives in one pure
   function, `aisle.mobility.guard.base_watchdog_reason`, evaluated
   identically from the pose handler and the tick sweep; pinned by unit
   tests, `test_wall_net_stops_latched_command_without_sim_stamps`
   (blind clock), and `test_tick_sweep_stops_latched_command_when_poses_cease`
   (hung sim — only the sweep can act). Because the guard's `tick` now
   carries a safety sweep, MOBILE_GUARD_INCOMPLETE requires it alongside
   `base_pose`.

Stamp hygiene at the trust boundary (PR #156 review): metadata from
upstream nodes is parsed TOTALLY (`parse_env_id` / `parse_sim_stamp`,
BG-3 — a malformed field degrades, it never kills the safety gate); a
stamp that is absent, zero (the `topics.stamp()` default for unstamped
producers), or malformed reads as "no sim clock", never as an anchor; a
REGRESSING stamp (bridge restart) re-anchors the staleness reference
with a stderr note instead of leaving the sim check silently open
forever; watchdog stops carry the last known sim stamp so traces can
locate them; and on `reset_done` the guard EMITS an explicit [0, 0] per
moving env — same channel as commands, so it orders after any in-flight
pre-reset command that would otherwise re-latch unwatched. The
validator's MOBILE_GUARD_INCOMPLETE additionally checks SOURCES, not
just port names: guard `tick` must be a dora timer at <= 5000 ms and
guard `base_pose` must come from a `sim_bridge` provider — an
agent-authored graph can no longer satisfy the rule while feeding the
watchdog a forged clock (the fuller `is_clock` manifest marker remains
follow-up).
4. **The validator's MOBILE_GUARD_INCOMPLETE rule requires `base_pose`
   AND `tick`** — the pose stream carries keep-out feedback plus the
   watchdog's sim clock; the stats tick carries BG-5 plus the wall-net
   sweep. Either missing silently disables a safety mechanism, the
   exact class this rule exists to catch.

Queue-depth choices, deliberately asymmetric: waypoint-nav's `base_pose`
is its CLOCK, so it moves from latest-wins to `queue_size: 10` — deep
enough that scheduler jitter never drops a clock edge (dropping edges
would make the control-iteration count a function of host load), shallow
enough to bound the stale-pose replay burst under pathological load to
0.2 sim-s of history. The GUARD's `base_pose` stays `queue_size: 1`:
for keep-out, the freshest pose is the safety-correct semantics, the
sim-stamp staleness math is unaffected by dropped intermediate poses
(it compares stamps, not event counts), and the wall net bounds every
missed-event scenario. A validator-enforced `is_clock` manifest marker
(so no future graph can rewire a clock input latest-wins) was considered
and left as follow-up.

Why sim-time staleness is the right semantics, not just the
deterministic one: the hazard of a latched stale command is the sim
TRAJECTORY it drives (distance = v x sim seconds). A wall-clock
staleness window covered a different amount of trajectory at every rtf;
`base_staleness_s = 0.5` now bounds the runaway trajectory identically
on every host (CON-5's outcome-relevant frame).

Watchdog liveness argument: if the BRIDGE dies, poses stop but so does
physics — there is nothing to stop. If the command PRODUCER dies while
the sim runs (the case the watchdog exists for), poses keep flowing and
sim-stale fires. The watchdog's clock is alive exactly when the base
can move.

## What this does NOT fix

- **Pipeline wall latency still quantizes into sim ticks** (every
  tier): when a computed command reaches the bridge remains
  wall-dependent until the sim-clock lockstep decision (SPEC 030/010,
  open — ADR-25 residual 1). This ADR removes the S1-only TIMER
  channel; idle-machine ops discipline (ADR-26) still applies.
- **The arm/base mutex hold window (`arm_motion_hold_s`) is still WALL
  seconds** (`base_creep_deadline` on the injected clock). Same species
  of rtf coupling — how much sim trajectory the creep clamp covers
  depends on host speed — but retiming it needs a sim-time reference on
  the ARM command path and a semantic review of the 1.0 s value.
  Follow-up, tracked in issue #71.
- Metal ULP nondeterminism (ADR-26 layer d) is untouched: attested S1
  pairs remain statistical at the outcome layer.
- **Adversarial-review residuals accepted as documented behavior**
  (PR #156): (a) under a guard backlog, the latest-wins pose queue can be
  serviced before the queued command backlog, firing a transient
  (fail-safe) stale stop whose trace placement is host-dependent — the
  wall-latency residual's species, not new nondeterminism in healthy
  runs; (b) a hung sim now silences the nav action entirely (no wall
  heartbeat by design — the harness wall-clamp relaunch is the recovery
  path), and MOB-2's ">=2 Hz" feedback is a SIM cadence under this ADR (a
  wall reading at very low rtf would dip below 2 Hz; a spec clarification
  is the owner's call); (c) a wall-timed-out latched base on a HUNG sim
  is stopped by the sweep within ~5 s rather than the old 50 ms — while
  the sim runs, the pose path still enforces at 50 Hz, and a hung sim
  moves nothing; (d) draining the nav pose queue to newest under backlog
  was proposed (performance) and REJECTED: coalescing would make the
  control-iteration count a function of host load again, the exact
  channel this ADR removes.

## Acceptance

- `tests/graph/test_guard_mutex.py::test_watchdog_stops_latched_base_command`
  (MOB-3): the stale watchdog fires from sim-stamped poses, no timer.
- `tests/graph/test_guard_mutex.py::test_wall_net_stops_latched_command_without_sim_stamps`
  (MOB-3): an unstamped pose source + dead producer is still stopped, by
  the wall net — the watchdog never fails open.
- `tests/accept/test_contract_mobile.py::test_nav_action_lifecycle`
  (MOB-2): the pose-driven nav still walks goal -> >=2 Hz feedback ->
  success under one goal_id.
- `tests/unit/test_mobility.py::TestBaseWatchdogReason` (MOB-3, CON-5):
  the full verdict table — sim-stale, wall-silent, timeout precedence,
  pre-pose commands.
- `tests/unit/test_validator_mobile.py`: MOBILE_GUARD_INCOMPLETE
  requires base_pose + tick; a guard missing either fails closed.
