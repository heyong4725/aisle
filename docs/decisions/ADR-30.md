# ADR-30 — Run-to-quiescence simulation turns close issue #71's wall-latency channel

Status: PROPOSED (agent-drafted 2026-08-11 at the owner's direction; ratified
iff the owner merges the `spec-change` PR carrying it, which is the CON-10/14
human review). Relates to ADR-25 (reset-anchored startup), ADR-26 (layered
reproducibility), ADR-29 (sim-anchored S1 timers), SPEC 010 TC-2/TC-4,
SPEC 030 BRG-1, SPEC 050 CAP-1, SPEC 060 VAL-2, SPEC 210 MOB-2/MOB-3, and
CON-5.

## Problem

Issue #71 isolated three different kinds of nondeterminism. ADR-25 removed the
startup reset race, ADR-26 gave irreducible Metal ULP noise the statistical
semantics the platform can actually provide, and ADR-29 removed S1's two
wall-timer-driven control loops. One systematic channel remains on every tier:
the bridge advances physics on a 10 ms WALL timer while a multi-process policy
computes. The wall latency of an observation -> planner -> command path is
therefore rounded into a variable number of simulated steps. The measured M0
pair applied its first `joint_cmd` at 0.61 s versus 0.79 s under load even
though its seed artifacts and reset were identical. This is not GPU noise: it
changes the simulated trajectory before CON-5's 1.0 s exact comparison window
has ended.

Three smaller contract holes have the same root:

- an input used as a simulated clock can still be wired latest-wins because a
  capability manifest cannot identify clock inputs;
- the mobile arm/base exclusion remembers arm motion for 1.0 WALL seconds, so
  the amount of simulated motion covered changes with realtime factor;
- MOB-2 says navigation feedback is at least 2 Hz without saying which clock.

## Decision

### 1. Attested simulation is a sequence of closed turns

An attested or acceptance simulation uses BRG-1 lockstep mode. `turn_id` is a
process-lifetime monotonic UInt64 independent of simulated time (a reset can
open a new turn without taking a physics step). Let `S(k)` be the realized
scene when turn `k` opens.

1. The bridge publishes all observations due at `S(k)` and a `sim_turn`
   watermark (`sim_turn_u64`, value `k`). Every message in the turn carries
   matching `turn_id` metadata. The watermark declares parallel
   `closed_outputs: list[str]` and `emitted_counts: list[int]` metadata, in
   lexical port order, so a receiver knows exactly how many same-turn messages
   precede closure on each subscribed output. The initial reset opens turn zero
   at the injected state before any physics step.
2. Every stateful node on a path that can affect a bridge command, reset, or
   episode verdict consumes the turn in causal order. It forwards a watermark
   only after it has received every count declared by every causal upstream,
   consumed those messages, and emitted all of its own. Its watermark declares
   its output counts by the same rule. Closure therefore does not assume dora
   orders different output ports, and absence of a command is a deterministic
   zero count rather than a timeout guess.
3. The terminal barrier emits one `turn_commit` only after every relevant
   branch has closed. For a motion turn, the bridge applies commands in
   canonical port order and advances physics exactly once to `S(k+1)`. For a
   reset turn, it discards same-turn motion, injects the reset state, advances
   no physics, and opens `S(k+1)` with the next `turn_id`.
4. A missing, duplicate, stale, or future watermark/commit is a loud protocol
   error. A WALL watchdog may abort a hung dataflow, but it MUST NOT advance
   physics, manufacture a commit, or otherwise change simulated state.

At most one turn is open. This makes the sequence of step assignments a
function of the graph and its stamped messages, not of how quickly processes
were scheduled. It also bounds memory naturally: no unbounded backlog of
simulation turns can accumulate.

Free-running wall-tick mode remains useful for reset-less bridge bring-up and
interactive visualization, but its run manifest is non-attesting and it is not
eligible for acceptance or campaign metrics. `AISLE_STEP_WITHOUT_RESET=1`
continues to mean bring-up, not a second reproducibility contract.

This decision defines the observable protocol, not one privileged
implementation. A generic node wrapper may implement watermark bookkeeping,
or nodes may implement it directly. Either way the validator must prove the
same topology and the graph acceptance test must demonstrate the same
run-to-quiescence behavior.

### 2. Sim-clock inputs are declared and lossless

CAP-1 gains optional `is_clock: true` on an input declaration (default false).
Such an input is a causal watermark/clock edge, not ordinary latest state. Its
graph edge must set `queue_policy: backpressure` and a positive explicit queue
size. With at most one turn open this queue cannot build a legitimate backlog;
an overflow is a protocol failure, never a reason to drop a clock edge. Its
source must be the bridge's simulation clock or a clock participant, and the
node must preserve the stamp when it closes the turn. The validator rejects
drop-oldest/latest-wins clock edges, untrusted clock sources, missing clock
participation on a state-changing path, cycles that do not cross the bridge
turn boundary, and multiple terminal commits.

Clock messages use the closed CAP-2 schema `sim_turn_u64` (`UInt64[1]`, the
turn id) and carry the turn's `sim_time_ns` metadata. The bridge output is
`sim_turn`, participant watermarks conventionally use `turn_done`, and the
single bridge input is `turn_commit`; different names do not create different
schemas or semantics. Every watermark also carries parallel
`closed_outputs`/`emitted_counts` lists in lexical port order. Receivers count
messages by `(source, output, turn_id)` and do not infer closure from transport
arrival order.

`is_clock` does not mean every state topic is a clock. For example, ADR-29
deliberately keeps budget-guard `base_pose` latest-wins for geometric safety;
the new turn watermark, rather than the pose queue, proves quiescence. A node
may still use a state topic as its control cadence, but it cannot use dropped
state messages as the structural barrier.

### 3. All simulated safety windows use the contract clock

The mobile arm/base exclusion's `arm_motion_hold_s` is measured from the
changed `joint_cmd`'s contract stamp. In simulation that is simulated time; on
hardware it is the driver's monotonic device-time stamp. A process wall clock
is only a fail-closed liveness backstop and cannot shorten or extend the
simulated exclusion. Missing or malformed stamps fail closed while the arm is
potentially moving.

MOB-2's navigation feedback rate follows TC-4: at least 2 Hz in simulated time
under simulation and at least 2 Hz wall/device time on hardware. The existing
ADR-29 pose-driven 50 Hz SIM implementation satisfies the clarified contract.

## Deterministic ordering and reset boundary

The bridge accepts only messages whose `turn_id` matches the one open turn. A
participant must preserve both `turn_id` and the originating `sim_time_ns`
across derived outputs; silently replacing either with zero or with the
receiver's current time is a protocol error. Within a motion turn the bridge's
precedence is:

1. reset (at most one; duplicate requests fail),
2. `joint_cmd`,
3. `gripper_cmd`,
4. `base_cmd`.

There is one producer per bridge command input after validation, so port order
is sufficient and arrival order is irrelevant. Reset closes the old episode,
drops its motion commands, injects the new state, resets publish schedulers,
and opens the next globally numbered turn with episode-relative step zero.
`reset_done` participates in that new turn's barrier; physics cannot outrun
goal/planner initialization after a reset.

## Why not the alternatives

- **A larger fixed WALL delay** only moves the race. Any finite delay can be
  exceeded by host load, and a load-dependent timeout is still a different
  simulated trajectory.
- **Apply a command at `source_stamp + N` without a barrier** makes on-time
  commands deterministic but cannot distinguish "no command" from "late
  command". A late arrival still makes the run depend on the scheduler.
- **Pause only while motion is active** leaves the first planner command and
  episode/reset transitions wall-quantized — the exact M0 failure.
- **Collapse every expert into the bridge process** would remove IPC races but
  destroy the node-swap architecture this project is evaluating.
- **Assume a trailing watermark arrives after all data outputs** relies on a
  cross-port ordering guarantee dora does not document. Declared per-port
  counts make closure independent of transport arrival order.
- **Treat the channel as ADR-26 statistical noise** is rejected. Metal ULPs are
  irreducible on the required backend; wall-to-sim step assignment is a
  discrete orchestration choice we can and must make deterministic.

## Migration and acceptance

This is a Class C change. Implementation requires one human-reviewed
`env-change` epoch because every measured `graphs/expert_*.yaml` must join the
barrier. The implementation PR must, as one coherent migration:

- implement bridge turn/open/commit handling and total stamp parsing;
- provide the participant/watermark helper and migrate every state-changing
  path in every measured expert graph;
- add `is_clock` to the manifest JSON schema and relevant manifests;
- add validator failures and golden graphs for dropped/untrusted/incomplete
  clock topology and multiple commits;
- retime `arm_motion_hold_s` onto the contract stamp with fail-closed tests;
- update the frozen-set hash and validate every expert graph;
- run two deliberately different wall-delay schedules and prove identical
  command-to-turn assignment plus CON-5 layers (a)-(c) through 1.0 sim-s.

The attested seed-100 S1 pair requested in issue #71 is the post-merge
measurement. Its episode result may still flip under ADR-26 layer (d); the
pass condition here is identical seed artifacts, exact turn/reset timing, and
physics agreement within 1e-6 through the complete comparison window.
