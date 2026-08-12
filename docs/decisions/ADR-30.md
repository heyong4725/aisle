# ADR-30 — Run-to-quiescence simulation turns close issue #71's wall-latency channel

Status: PROPOSED (agent-drafted 2026-08-11 at the owner's direction; amended
2026-08-12 after the PR #171 protocol review; ratified iff the owner merges
the `spec-change` PR carrying it, which is the CON-10/14 human review).
Relates to ADR-25 (reset-anchored startup), ADR-26 (layered reproducibility),
ADR-29 (sim-anchored S1 timers), SPEC 010 TC-2/TC-4, SPEC 030 BRG-1, SPEC 050
CAP-1, SPEC 060 VAL-2, SPEC 210 MOB-2/MOB-3, and CON-5.

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

### 1. Attested simulation is a sequence of closed, stratified turns

An attested or acceptance simulation uses BRG-1 lockstep mode. `turn_id` is a
monotonic UInt64 scoped by a `turn_epoch` (see §1.6) and independent of
simulated time (a reset can open a new turn without taking a physics step).
Let `S(k)` be the realized scene when turn `k` opens.

**1.1 Participants are defined topologically.** Classify every graph edge as
FORWARD (default) or EPISODIC (declared per §2). A node is a turn PARTICIPANT
iff it has a forward-edge path to a bridge command or reset input. Nodes with
no such path — the trace recorder, a held-out verifier in A7 mode, the
sidecar realistic verifier in `both` mode — are measurement taps: exempt by
construction, no declaration needed, and a slow tap can never stall physics.
Pure transforms on forward paths participate like any other node (they only
forward watermark bookkeeping); there is no "stateless" exemption, because a
non-watermarking hop would break the count chain for everything downstream.

**1.2 Turn lifecycle.**

1. The bridge publishes all observations due at `S(k)` and a `sim_turn`
   watermark (`sim_turn_u64`, value `k`). Every message in the turn carries
   matching `turn_id` metadata. The watermark declares parallel
   `closed_outputs: list[str]` and `emitted_counts: list[int]` metadata
   enumerating EVERY output port of the node in lexical order (count 0 is
   ordinary — rendering is sparse per BRG-2 — and omission of a port is a
   malformed watermark, so "closed with zero" is never confusable with "not
   yet declared"). The initial reset is consumed in turn zero, which the
   bridge opens at startup before any physics step.
2. Every participant consumes turn `k` in forward causal order: it closes
   only after receiving every count declared by every forward upstream for
   turn `k`, PLUS every count declared for its EPISODIC inputs by their
   producers' turn `k-1` watermarks (§1.3). It then emits its own outputs
   and watermark declaring its counts by the same rule. Closure therefore
   does not assume dora orders different output ports, and absence of a
   command is a deterministic zero count rather than a timeout guess.
3. The terminal barrier emits one `turn_commit` only after every forward
   branch has closed. For a motion turn, the bridge applies commands in
   canonical port order and advances physics exactly once to `S(k+1)`. For a
   reset turn, it discards same-turn motion, injects the reset state,
   advances no physics, and opens `S(k+1)` with the next `turn_id`.
4. A missing, duplicate, stale, or future watermark/commit is a loud protocol
   error. A WALL watchdog may abort a hung dataflow, but it MUST NOT advance
   physics, manufacture a commit, or otherwise change simulated state. The
   watchdog budget is PER TURN TYPE (§1.5): ordinary turns get a tight
   budget, turns that legitimately carry heavy causal work (an A7 episode
   verdict) get the work's own configured budget.

At most one turn is open. The sequence of step assignments is a function of
the graph and its stamped messages, not of how quickly processes were
scheduled, and no unbounded backlog of simulation turns can accumulate.

**1.3 Episodic edges: cycles are stratified, not forbidden.** The TC-6
service and TC-7 action patterns are bidirectional by construction —
`rollout-client ↔ verifier` and `budget-guard → violation →
task-state-machine → target_request → … → budget-guard` are cycles in every
real AISLE graph, and none of them crosses the bridge. A closure rule that
waits on ALL causal inputs therefore cannot terminate (PR #171 review). The
resolution: reply/verdict/notification back-edges are declared EPISODIC
(§2). A message emitted on an episodic edge in turn `k` is consumed at the
opening of the receiver's turn `k+1` — its count is known from the
producer's turn-`k` watermark, so consumption is exact and deterministic,
delayed by exactly one turn. Closure of turn `k` never waits on episodic
messages OF turn `k`. The forward-edge graph must be acyclic (except through
the bridge commit); cycles are legal iff every cycle contains at least one
episodic edge, and the validator checks exactly that (VAL-2 CLOCK_CYCLE).

**1.4 Participants do not emit turn-stamped messages from wall handlers.**
Wall-timer inputs on participants (the client's tick, the state machine's
tick, the guard's stats tick) may abort, log, or emit NON-turn diagnostics
(`guard_stats`), but every turn-stamped emission — resets, goals, target
requests, commands, safety stops — happens in a turn-edge or episodic-input
handler. Concretely: the rollout client consumes `episode_result` /
`reset_done` as episodic inputs and emits the next `reset` or `episode_goal`
at its next turn opening, stamped with that turn's id; a verdict in turn `k`
yields a reset in `k+1` and a new episode opening `k+2` — deterministic,
never wall-quantized. The "boot reset proposes turn zero" framing is
replaced: only the bridge opens turns, starting at zero on startup.

**1.5 Heavy causal work runs inside its turn.** In A7 mode the realistic
verifier is a participant (its verdict drives the loop). Its 3–5 s judge
runs INSIDE the episode-end turn: wall duration of a turn is unbounded by
design, and the watchdog budget for a verdict-bearing turn is the judge's
configured budget, not the ordinary-turn budget. The alternative — letting
the verdict land in whichever turn the wall-clocked judge finishes — is
exactly the wall-scheduling this ADR forbids. In `both` mode the same node
has no forward path to the bridge and is a measurement tap (§1.1).

**1.6 Turn epochs.** `turn_id` is scoped by `turn_epoch`, an integer the
bridge increments per process start, carried on every watermark. The ADR-23
wall-clamp relaunch is the sanctioned recovery path, and without the epoch a
surviving participant's queued watermark from the old incarnation would
alias the new run's turn zero. Cross-epoch messages are stale by definition
and fail loudly.

Free-running wall-tick mode remains useful for reset-less bridge bring-up and
interactive visualization, but its run manifest is non-attesting and it is not
eligible for acceptance or campaign metrics. `AISLE_STEP_WITHOUT_RESET=1`
continues to mean bring-up, not a second reproducibility contract. Bring-up
graphs never reach the validator at all (ADR-25: `BRINGUP_ENV_FORBIDDEN`
rejects the env key in validated graphs, and bring-up graphs run directly via
`dora run`), so the CLOCK_* checks apply unconditionally to every validated
graph — there is no validator-level free-run exemption.

This decision defines the observable protocol, not one privileged
implementation. A generic node wrapper may implement watermark bookkeeping,
or nodes may implement it directly. Either way the validator must prove the
same topology and the graph acceptance test must demonstrate the same
run-to-quiescence behavior.

### 2. Sim-clock inputs are declared, and edges are classified

CAP-1 gains two optional input fields (default false / forward):

- `is_clock: true` — a causal watermark/clock edge, not ordinary latest
  state. Its graph edge must set a positive explicit queue size with
  `queue_policy: backpressure`.
- `turn_edge: episodic` — a §1.3 back-edge (replies, verdicts, results,
  violation notifications) consumed at the receiver's next turn opening.

HONEST TRANSPORT NOTE (PR #171 review, verified against the pinned dora rev
`cd597e70`): dora's `backpressure` policy is NOT lossless — it multiplies the
effective cap by 10 (minimum 100) and at that hard cap the event scheduler
DROPS OLDEST with an error log (`apis/rust/node/src/event_stream/
scheduler.rs`). The protocol's enforcement is therefore NOT the queue: with
at most one turn open, a clock queue holds at most a handful of events, and
if an edge is ever dropped the declared counts can never be satisfied — the
barrier hangs and the WALL watchdog aborts loudly without touching simulated
state. `dropped` counters and `turn_id` continuity are the detectors.
Backpressure is still required because it buys a 10x cap over drop-oldest
semantics at the same declared size.

Clock messages use the closed CAP-2 schema `sim_turn_u64` (`UInt64[1]`, the
turn id) and carry the turn's `sim_time_ns` and `turn_epoch` metadata. The
bridge output is `sim_turn`, participant watermarks conventionally use
`turn_done`, and the single bridge input is `turn_commit`; different names do
not create different schemas or semantics. Receivers count messages by
`(source, output, turn_id)` and do not infer closure from transport arrival
order.

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

The ADR-29 wall net (`base_stale_wall`) exists for paths where the sim clock
is blind. In an attested lockstep graph the guard is a participant, its pose
input is turn-stamped, and physics cannot advance while a turn is open — so
the blind cases cannot occur, and the sim-time staleness check (`base_stale`,
pose-clocked, deterministic) is the sole stop path. The wall-net SWEEP's
turn-stamped emissions are therefore confined to non-attesting free-run and
hardware modes; in lockstep the sweep may abort but not emit commands
(§1.4). Without this, host load would convert the safety net into either a
rejected unstamped command or an attested-run protocol abort (PR #171
review).

MOB-2's navigation feedback rate follows TC-4: at least 2 Hz in simulated time
under simulation and at least 2 Hz wall/device time on hardware. The existing
ADR-29 pose-driven 50 Hz SIM implementation satisfies the clarified contract.

## Deterministic ordering and reset boundary

The bridge accepts only messages whose `turn_epoch` and `turn_id` match the
one open turn. A participant must preserve `turn_epoch`, `turn_id`, and the
originating `sim_time_ns` across derived outputs; silently replacing any with
zero or with the receiver's current time is a protocol error. Within a motion
turn the bridge's precedence is:

1. reset (at most one; duplicate requests fail),
2. `joint_cmd`,
3. `gripper_cmd`,
4. `base_cmd`.

There is one producer per bridge command input after validation, so port order
is sufficient and arrival order is irrelevant. Reset closes the old episode,
drops its motion commands, injects the new state, resets publish schedulers,
and opens the next globally numbered turn with episode-relative step zero.
`reset_done` is an episodic edge into the client (§1.3); physics cannot
outrun goal/planner initialization after a reset because the goal itself is a
forward input of the turn that follows.

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
- **Forbid cycles outright instead of stratifying** would reject every real
  graph: TC-6/TC-7 make request/reply and action loops structural. One turn
  of delay on declared back-edges keeps them deterministic (PR #171 review).
- **Treat the channel as ADR-26 statistical noise** is rejected. Metal ULPs are
  irreducible on the required backend; wall-to-sim step assignment is a
  discrete orchestration choice we can and must make deterministic.

## Cost

A per-turn barrier is roughly (forward-path depth) IPC hops per 10 ms of
simulated time — order 6,000 turns for a 60 sim-s desk episode. The
implementation targets a barrier overhead of ~1 ms wall per ordinary turn and
MUST measure lockstep versus free-run wall cost on `expert_t0` (60 sim-s) and
`expert_s1`. Acceptance: attested-mode wall cost within 2x of free-run at
equal load, OR an explicit ADR-21 re-budget signed alongside the env-change —
campaign wall ceilings are frozen, so an unbudgeted slowdown spends real
ceiling silently.

## Migration and acceptance

This is a Class C change. Implementation requires one human-reviewed
`env-change` epoch because every measured `graphs/expert_*.yaml` must join the
barrier. The implementation PR must, as one coherent migration:

- implement bridge turn/epoch/open/commit handling and total stamp parsing;
- provide the participant/watermark helper and migrate every forward path in
  every measured expert graph;
- retime the rollout client and task-state-machine off wall ticks (§1.4):
  turn-stamped emissions only from turn-edge/episodic handlers; declare the
  episodic edges (`episode_result`, `reset_done`, `violation`, `nav_result`,
  service replies) with `turn_edge: episodic`;
- gate the ADR-29 wall-net sweep's command emissions on non-attesting modes
  (§3) with a regression test that a lockstep run under host load neither
  aborts nor emits an out-of-turn stop;
- add `is_clock` and `turn_edge` to the manifest JSON schema and relevant
  manifests;
- add validator failures and golden graphs for dropped/untrusted/incomplete
  clock topology, forward-edge cycles, and multiple commits — including a
  GOOD golden graph containing the client/verifier and guard/state-machine
  cycles with their episodic declarations;
- retime `arm_motion_hold_s` onto the contract stamp with fail-closed tests;
- set per-turn-type watchdog budgets (ordinary vs verdict-bearing turns);
- update the frozen-set hash and validate every expert graph;
- measure the lockstep/free-run wall-cost ratio per the Cost section;
- run two deliberately different wall-delay schedules and prove identical
  command-to-turn assignment plus CON-5 layers (a)-(c) through 1.0 sim-s.

Until that epoch lands, runs remain governed by the pre-ADR-30 BRG-1 wording
(the "Current" contract in SPEC 030): ratifying this ADR changes what the
implementation must become, not what today's runs attest. SPEC amendments
carrying lockstep wording are declarative pre-implementation per the SPEC 040
preamble convention and are upgraded to RFC-2119 MUST by the implementing PR
together with the citing tests (HAR-9).

The attested seed-100 S1 pair requested in issue #71 is the post-merge
measurement. Its episode result may still flip under ADR-26 layer (d); the
pass condition here is identical seed artifacts, exact turn/reset timing, and
physics agreement within 1e-6 through the complete comparison window.
