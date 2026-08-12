# AISLE — A Substrate for Autonomous Robot Engineering

**Agentic In-Store Learning Environment · Technical report · August 2026**

*Status snapshot: 2026-08-12. Measured results cite the
[README status table](../README.md#status), which is canonical. Forward-looking
sections are labelled as such and describe committed design direction, not
shipped capability.*

---

## Contents

1. [Executive summary](#1-executive-summary)
2. [The problem: why "the demo worked" is not evidence](#2-the-problem-why-the-demo-worked-is-not-evidence)
3. [The research question](#3-the-research-question)
4. [The architectural bet: typed dataflow](#4-the-architectural-bet-typed-dataflow)
5. [System architecture](#5-system-architecture)
6. [Determinism as an engineered property](#6-determinism-as-an-engineered-property)
7. [The evidence architecture](#7-the-evidence-architecture)
8. [The experimental program](#8-the-experimental-program)
9. [What has been measured](#9-what-has-been-measured)
10. [The model agenda: VLA, world models, and WAMs](#10-the-model-agenda-vla-world-models-and-wams)
11. [Safety under learned policies](#11-safety-under-learned-policies)
12. [Threats to validity](#12-threats-to-validity)
13. [Positioning](#13-positioning)
14. [Roadmap](#14-roadmap)
15. [Why this matters](#15-why-this-matters)
16. [Appendix A: notation](#appendix-a-notation)
17. [Appendix B: reading path](#appendix-b-reading-path)

---

## 1. Executive summary

AISLE asks whether AI coding agents can autonomously build, diagnose, improve,
reuse, and safely operate robotic systems — and it is built so that the answer
can be *measured* rather than demonstrated.

The setup: a pharmacy-style manipulation environment in the
[Genesis](https://genesis-world.readthedocs.io/) physics simulator, driven by
[dora-rs](https://github.com/dora-rs/dora) dataflows. Coding agents (Claude
Code, Codex) compose and evolve those dataflows. Every node is a typed,
discoverable capability with a manifest. Every graph is statically validated
before it can run. Every motion command traverses a safety guard the agent
cannot bypass. Every episode is judged by a verifier the agent cannot edit,
and every run is attested against a hash of the frozen task definition.

Three properties make this different from a robotics demo repository:

**The engineering loop is the object of study, not the robot task.** The robot
task is the instrument. What is measured is whether an autonomous agent can run
a credible engineering process around a modular physical-AI system: compose,
validate, execute under budget, read typed evidence, diagnose, improve, and
carry evaluated skills forward.

**Evidence discipline is structural, not procedural.** A hypothesis must be
logged before the run that tests it. The scene, scorer, reset, safety limits,
expert baselines, and campaign budgets are hash-frozen. A run whose code,
treatment, or runtime drifted is flagged inadmissible by a post-run audit and
cannot silently become verdict evidence. These are properties of the harness,
not rules in a document.

**The substrate is designed for models it does not yet contain.** AISLE is
model-light today by deliberate experimental control: a classical perception,
planning, and control pipeline gives a clean baseline and isolates the
*engineering* question from the *policy* question. VLA policies, world-model
planners and environments, and World Action Models enter as typed, swappable
nodes behind the same action adapters, guard, verifier, and evidence contract.
That turns "classical pipeline vs. learned policy vs. predictive hybrid" into a
matched systems experiment — one the coding agent can itself propose and run.

The whole thing reproduces on a MacBook.

---

## 2. The problem: why "the demo worked" is not evidence

Robot learning has an evaluation problem, and agentic robot *engineering* has a
worse one.

A robot demonstration establishes that some configuration of code, scene,
seeds, hardware, and operator attention produced a success. It does not
establish which of those was responsible. When an autonomous agent is the thing
modifying the system, the confound space widens sharply, because the agent can
change the very things a fair comparison must hold fixed.

Consider a coding agent that reports improving task success from 60% to 90%.
Absent structural controls, at least seven explanations are consistent with
that number:

1. The policy genuinely improved.
2. The agent drew easier seeds on the second run.
3. The agent edited the scorer, or the failure taxonomy, so more outcomes count
   as success.
4. The agent edited the scene, making the task easier.
5. The agent widened the safety envelope, trading unmeasured risk for success.
6. The environment drifted — a dependency, a backend, a driver.
7. The agent spent far more compute on the second run.

Each of these is a *plausible* behavior for an optimizer pointed at a metric.
Several have been observed in practice in this project's own campaign history,
which is why the countermeasures below exist as mechanism rather than policy.

AISLE's response is to make each explanation either impossible or visible:

| Confound | Structural countermeasure |
|---|---|
| Easier seeds | Seeds are declared in the run manifest; episode records carry their seed. |
| Changed scorer | The verifier is in the hash-frozen set. Changing it invalidates the attestation. |
| Changed task | The scene is frozen on the same hash. |
| Widened safety envelope | The guard and `env/limits.toml` are frozen; violations are recorded topics, not log lines. |
| Environment drift | Dependency selection, backend, device, and a frozen-set hash ride with every run; a post-run audit compares them. |
| More compute | A tamper-evident budget ledger meters episodes, wall time, and tokens against campaign ceilings. |
| Post-hoc storytelling | The idea gate requires the hypothesis to be logged *before* the run. |

None of this is exotic. It is ordinary experimental hygiene, moved from the
researcher's discipline into the runtime, because the "researcher" is an
autonomous process running unattended.

---

## 3. The research question

> **Can AI coding agents autonomously build, diagnose, improve, reuse, and
> safely operate robotic systems when those systems are composed as typed dora
> dataflows?**

The question decomposes into five falsifiable claims. Each is registered as a
hypothesis with a stated threshold *before* the campaign that tests it.

| Id | Claim | What would falsify it |
|---|---|---|
| **H1** | Zero-shot graph composition reaches ≥80% | Agents cannot assemble a launchable typed graph from a registry without iteration |
| **H2** | The evaluate-and-improve loop reaches ≥90% pass@1 | The agent cannot close the gap from a working graph to a performant one |
| **H3** | A persistent evaluated skill library makes later tasks ≥2× faster | Accumulated skills do not transfer; each task pays full cost |
| **H4** | Hot-swap iteration beats an equal-budget monolithic-script control | The substrate's structure costs more than it saves |
| **H5** | Wrong-object outcomes stay at zero under free agent iteration | Free iteration produces unsafe behavior the guard does not catch |

**H5 is the safety hypothesis and is stated as a quantity that must remain
zero.** It is reported with an explicit denominator rather than a percentage,
because "99.5% safe" is not a meaningful claim about a system that hands
medication to a person. `wrong_object` — delivering the wrong medicine — is
the failure class the entire perception and verification stack exists to
prevent, and the one the task's design makes ten times worse than a timeout.

The claims are deliberately separable. A negative result on H4 — the typed
substrate losing to free-form scripting — is publishable and interesting. The
project is not constructed to confirm its own architecture.

### 3.1 Two loops, deliberately separated

The system contains two nested loops, and conflating them is the most common
way to misread a result.

```text
OUTER (between episodes, minutes-to-hours) — the research loop
  coding agent: compose graph → validate → run campaign → read traces
              → diagnose → change nodes/params → register skill → repeat

INNER (within an episode, milliseconds)     — the control loop
  observe → perceive → plan → guard → actuate → verify
```

The outer loop is what H1–H4 measure: can an autonomous agent conduct
engineering? The inner loop is what a policy comparison measures: is this
architecture good at the task? They have different units of replication —
independent agent sessions for the outer, episodes for the inner — and
different failure modes.

Keeping them separate is what makes the model agenda coherent. A VLA changes
the inner loop. Whether the agent can *decide to reach for* a VLA, evaluate it
honestly, and reuse it later is an outer-loop question. A system that only
measured the inner loop would report "the VLA is better" and miss the actual
research question.

---

## 4. The architectural bet: typed dataflow

The central design decision is that agent-authored robot systems should be
**graphs of typed, independently-executable nodes** rather than monolithic
scripts. Everything else follows from it.

### 4.1 What the bet buys

**Composition becomes a search over a typed space.** An agent selecting from a
registry of capabilities with declared inputs, outputs, schemas, rates, and
embodiments is doing constrained search. An agent writing a Python script is
doing unconstrained generation. The former can be statically validated before
anything moves; the latter can only be run.

**Failure becomes localized and attributable.** When a graph node fails, the
failure has a name, a process, a topic boundary, and a trace. When a script
fails, the failure is "the script failed". Attribution is the difference
between an agent that can diagnose and an agent that can only retry.

**Substitution becomes the unit of change.** A node honoring the topic contract
can be replaced by any other node honoring it — a different grasp planner, a
different perception stack, a simulated driver swapped for a hardware driver,
or a classical policy swapped for a learned one. This is what makes the model
agenda in §10 a *swap* rather than a rewrite.

**Safety becomes structural.** If every motion command must traverse a guard
node, and the validator rejects any graph where a command path bypasses it,
then safety is a property of the graph topology rather than of the policy's
good behavior. An agent cannot forget to be safe.

**Evidence becomes free.** Typed topics can be recorded generically. The
harness attaches a trace recorder to every declared endpoint without knowing
what any of them mean.

### 4.2 What the bet costs

Honesty requires naming the tax. An agent cannot move until the registry,
manifests, and validator exist — real engineering before any research result.
If the manifests are wrong, agents are constrained into a *worse* action space
than free-form code would give them. Schema friction is a real cost paid on
every task.

This is precisely what ablations A1 and A3 and hypothesis H4 measure. The bet
is falsifiable, and the project budgets for the possibility that it loses.

---

## 5. System architecture

```text
                    ┌──────────────────── coding agent (outer loop) ─────────────────────┐
                    │  compose · validate · run · read traces · diagnose · improve       │
                    └───────────────────────────────┬───────────────────────────────────┘
                                                    │ authors / swaps nodes
                                                    ▼
   ┌────────────────────────── dora dataflow (inner loop, per episode) ──────────────────────┐
   │                                                                                          │
   │   ┌──────────────┐   rgb/depth/seg    ┌───────────────┐   target    ┌────────────────┐   │
   │   │              │ ─────────────────▶ │  perception   │ ──────────▶ │   planning     │   │
   │   │  dora-genesis│   joint_state      │  (rung-gated) │             │  grasp / IK    │   │
   │   │    bridge    │ ─────────────────▶ └───────────────┘             └───────┬────────┘   │
   │   │              │                                                          │ joint_cmd  │
   │   │  owns the    │                    ┌───────────────┐                     ▼            │
   │   │  Genesis     │ ◀───────────────── │ budget guard  │ ◀───────────────────┘            │
   │   │  scene       │   *_cmd_safe       │  MANDATORY    │   every motion command           │
   │   │              │                    └───────────────┘                                  │
   │   │              │   oracle_state     ┌───────────────┐   episode_result                 │
   │   │              │ ─────────────────▶ │   verifier    │ ──────────▶ rollout client        │
   │   └──────────────┘   (verifier-only)  └───────────────┘                                  │
   └──────────────────────────────────────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼
              Arrow traces · episode records · video · manifest · env hash · budget ledger
```

### 5.1 The dataflow substrate

Nodes are OS processes. Edges are typed topics carrying Apache Arrow payloads.
The runtime is dora-rs, pinned to an exact revision so that scheduler
semantics are part of the attested environment rather than an ambient variable.

Queue behavior is part of the contract and is chosen per edge. A control input
that must not miss an event gets an explicit deep queue; a state input where
only the newest value matters is left latest-wins. This is not a detail — it
determines whether a node reads the present or replays a backlog, and getting
it wrong produces some of the subtlest failures in the system. A camera stream
feeding a slow consumer that reads *the now* must be latest-wins; a camera
stream feeding a consumer that replays a *bounded past window* must be deep.
Both patterns exist in this repository, for those reasons.

### 5.2 The topic contract

`specs/010-topic-contract.md` (`TC-*`) is the document that makes sim→real a
node swap. It fixes:

- **Units and frames.** Radians, meters, robot base frame, quaternions `(x,y,z,w)`.
- **Topics, schemas, and rates.** `joint_state` as `Float32[n_dof]` at 100 Hz;
  `rgb_overhead` as flat `UInt8[h*w*3]` at 30 Hz with `h`/`w`/`enc` metadata;
  and so on. Consumers may not assume resolution.
- **Mandatory metadata on every message.** `sim_time_ns`, `env_id`, and a
  per-topic monotonic `seq`.
- **Rates as contracts, not hints.** Producers publish within ±20% of the
  declared rate. Under simulation, conformance is enforced against *simulated*
  time, with a wall-clock liveness floor — because a sub-realtime simulator is
  normal and must not be mistaken for a broken producer.
- **Service and action patterns.** Reset is a service: request/reply correlated
  by `request_id`. An episode is an action: goal, feedback, result correlated by
  `goal_id`. These two patterns carry essentially all of the system's
  request-scoped coordination.

The metadata rules look mundane and are load-bearing. `sim_time_ns` is what
lets every timing decision in the system be a function of simulated time rather
than host scheduling — the foundation of §6.

### 5.3 The bridge and the scene

One bridge node owns the Genesis scene per dataflow. It publishes observations,
accepts commands, steps physics, and services resets. Rendering is rate-limited
independently of physics, because rendering every camera every step is the
dominant cost in a simulator loop.

The scene is a pharmacy shelf with medicine boxes, a tray, and a robot arm
(Franka by default, SO-101 as an alternate embodiment; a mobile base profile
extends the contract with differential-drive topics and navigation as an
action). Box identity is carried by **printed label textures**, and colors are
permuted across medicines in the higher tiers so that color cannot predict
identity. That single design choice is what forces genuine perception rather
than a color lookup, and it is why the T2 tier is a real perception problem.

The scene, verifier, and reset live in the frozen set. They define the task and
the scoring, and they are exactly what an optimizer would otherwise edit.

### 5.4 The capability registry

Every node ships a manifest declaring: id, kind, what it `provides`, what it
`requires`, its inputs and outputs with schemas and rates, parameters with
types and ranges, the embodiments it supports, a safety class, an evalcard, its
origin (curated vs. agent-authored), and its source.

The manifest is what makes composition a typed search. `harness registry
search --provides grasp_planning --embodiment franka --installed` is the
agent's discovery surface, and every match reports whether it is actually
launchable — a distinction that turned out to matter enormously (§9).

The curated core is pinned exactly. Beyond it, the registry admits only
**registered skills**: agent-authored capabilities with a non-null evalcard,
admitted through a registration path that refuses to overwrite curated ids.
This is the mechanism behind H3's skill library — reuse is evidence-based
rather than reputational.

#### A manifest, concretely

A capability manifest is the agent's entire view of a node. Sketched:

```yaml
id: grasp-planner-topdown
kind: node
provides: [grasp_planning]
requires: [target_pose]
inputs:
  target_pose: {schema: pose7d_f32, rate_hz: 15}
outputs:
  grasp_pose: {schema: pose7d_f32, latency_class: fast}
params:
  approach_height_m: {type: float, default: 0.12, range: [0.05, 0.25]}
embodiment: {arm: [franka], gripper: parallel}
safety_class: motion
eval: {suite: t1_grasp, pass_rate: 0.94, last_run: "..."}
origin: hub
source: src/aisle/nodes/grasp_topdown.py
```

Four fields carry most of the weight. `provides`/`requires` make composition a
typed search. `schema` values come from a closed vocabulary, so a mismatch is a
validator error rather than a runtime surprise. `safety_class: motion` triggers
the rule that motion-class nodes need an evalcard. And `source` is compared
against the path dora will actually launch, so approved identities cannot front
unvetted code.

An agent-authored skill has the same shape with `origin: agent-authored` and a
non-null `eval` — the evalcard is what converts "I wrote a node" into "I wrote
a node that measurably works", and it is the unit H3's library accumulates.

### 5.5 The validator

`harness validate <graph.yaml>` is the most leveraged component in the system,
because **its error messages are the research agent's learning signal**. A
validator that says "invalid graph" teaches nothing; one that says "node X
requires capability Y, which is provided by installed manifest Z — wire
`Z/out` to `X/in`" teaches composition.

Every error carries a stable code and a hint naming a concrete fix. The codes
enforce, among others:

- **`INSTALL_MISSING`** — the manifest is schema-valid but its distribution is
  not installed, so the graph cannot launch. This was the single dominant H1
  failure mode.
- **`ORACLE_LEAK`** — privileged simulator truth may only be consumed by
  verifier nodes. A policy that reads `oracle_state` is cheating, and the
  validator refuses the graph rather than letting the run be discovered
  invalid afterwards.
- **`MOTION_UNGATED`** — every path terminating in a bridge motion input must
  traverse the budget guard. The validator rejects; it never rewires.
- **`PERCEPTION_RUNG_VIOLATION`** — a graph declaring perception rung L1 must
  not route ground-truth poses; at L2 it must route neither poses nor
  segmentation. The rung is declared in the graph, where the graph hash attests
  it.
- **`PATH_MANIFEST_MISMATCH`** — the file dora will actually launch must match
  the manifest's declared source, so unvetted code cannot execute under an
  approved identity.

The validator imports neither Genesis nor the dora runtime. It is pure, fast,
and unit-testable, which is why it can be run on every candidate graph an agent
produces.

### 5.6 Safety: the budget guard

The guard is a node, not a library, and every motion command traverses it. It:

- clamps joint and base velocities to configured limits;
- enforces keep-out zones (with an entry check, not merely a
  once-inside check — velocity is capped so a single step cannot cross the
  boundary);
- enforces arm/base mutual exclusion on the mobile profile, so a moving arm
  and a moving base cannot coexist above creep speed;
- runs staleness watchdogs that stop a latched command;
- **clamps rather than drops** — a malformed command becomes a safe hold with a
  recorded violation, never a silent no-op and never a crash.

Violations are published on a topic and recorded in traces, so "how often did
the guard intervene" is a measurable quantity rather than a log-grep. The guard
and its limits file are frozen.

The design principle throughout is **fail closed**: a malformed pose, an absent
stamp, a missing calibration, or an ambiguous state produces a refusal or a
hold, never a guess. This principle is applied recursively — even the parsers
that read message metadata are total functions that degrade to a safe value
rather than raising into an event loop, because a crashed safety node is an
unsafe safety node.

### 5.7 Verification and reset

Two verifiers run against the same episode:

The **oracle verifier** reads privileged simulator state and is the only ground
truth any metric may count. It emits a closed failure taxonomy: `wrong_object`,
`dropped`, `timeout`, `never_grasped`, `collision`. `wrong_object` triggers the
moment any non-target box enters the tray — a deliberate safety asymmetry that
does not wait for a timeout.

The **realistic verifier** judges from what a real deployment could see:
camera frames, calibration, and stage evidence. Its agreement with the oracle
is the **fidelity metric**, and the gap between them is a first-class research
result rather than a bug. In sidecar mode it observes without affecting control
flow; in the A7 treatment its verdict *drives* the loop while the oracle is
held out for scoring, which tests whether a portable verifier's noise breaks
learning.

**Reset** has two modes. Teleport restores object state directly. **Behavioral
reset** requires the robot to physically pick the delivered box from the tray
and re-shelve it through the guarded motion path, with bounded retry and
teleport fallback. Behavioral reset is parity with the real world, where
nothing teleports, and it is itself a skill the loop must maintain. Ablation A6
measures what teleporting hides.

### 5.8 The harness and evidence

`harness rollout` gates, instruments, executes, and records. Gating checks the
environment hash, validates the graph, and confirms an open idea. Instrumenting
attaches a trace recorder to every declared endpoint. Execution drives seeded
episodes through the reset→goal→result cycle. Recording produces, per run:

- **episode records** — one row per episode with seed, status, failure class,
  retries, and correlation id;
- **Arrow traces** — per-endpoint columnar logs with sim time, env id, seq, and
  payload;
- **video** of the overhead camera;
- **a manifest** carrying git SHA, environment hash, graph hash, dependency
  selection, simulation backend and device, seeds, budgets, and any relaunch
  history;
- **budget ledger entries** in a tamper-evident hash chain, where each entry
  hashes its predecessor, so an edited or dropped line breaks every hash after
  it.

Every CLI returns a single JSON object on stdout, logs to stderr, and exits
zero only on success — so agents can consume tools without parsing prose.

### 5.9 Hot swap and live iteration

A dataflow's nodes are processes, so a node can in principle be replaced
without restarting the run. `harness swap` does exactly that: it validates the
replacement against the same rules as a fresh graph, absolutizes its path from
the original graph's directory so that a staged replacement cannot validate one
file and launch another, and performs the substitution in the live dataflow.

This is the mechanism H4 measures. The claim is that iteration latency — the
wall time from "agent decides to change something" to "agent sees the effect" —
is the binding constraint on an autonomous research loop, and that a substrate
allowing node-level substitution beats one requiring a full relaunch,
particularly when the relaunch pays a multi-minute simulator build.

Measured at T0: hot-swap median 32.4 s vs. relaunch 41.8 s, with the mutation
mechanism alone about 1.7× faster. The honest caveats are in §9. The structural
point survives the small sample: a system where the expensive part (scene
construction) can be held across iterations has a different iteration economy
than one where it cannot, and that difference compounds over a campaign of
hundreds of iterations.

### 5.10 Fleet mode: batched environments

The bridge can own several environment instances simultaneously, with every
message carrying an `env_id` and per-environment routing of commands. Nodes that
should serve one environment declare a pin and ignore traffic for others;
unpinned nodes in single-environment graphs behave byte-identically, so the
feature costs nothing when unused.

This is the substrate for ablation A5 — one agent versus four versus eight on
batched environments — which asks whether agent throughput scales with parallel
environments or whether token cost grows super-linearly as the agent's context
fills with more concurrent state than it can track. That is a question about
agents, not about simulators, and it needs the environment axis to be cheap.

### 5.11 The sim-to-real path

Nothing in the topic contract mentions simulation. `joint_state` is
`Float32[n_dof]` at 100 Hz in the robot base frame whether it originates in
Genesis or in a hardware driver, and the metadata contract's `sim_time_ns`
becomes the driver's monotonic device time on hardware — which is why every
timing rule in the system is written against "the contract clock" rather than
"the simulation clock".

Consequently, the intended hardware path is a node swap: replace the bridge
with driver nodes speaking the same topics, and the perception, planning,
safety, and verification stack above it is unchanged. Two properties were
designed specifically to keep that path honest. The **behavioral reset**
removes the teleport convenience that hardware cannot provide. The **realistic
verifier** judges from camera evidence rather than privileged state, because
hardware has no oracle — and its measured disagreement with the oracle is the
number that predicts how much of the simulated result will survive the
transition.

No physical-robot evidence exists yet. The claim is architectural, and it is
listed among the threats to validity in §12.

### 5.12 A worked example: composing and running a T1 graph

Concretely, here is the loop an agent runs to solve T1 — "pick the named
medicine among five at randomized poses".

**Discovery.** The agent queries the registry for what it needs:

```
harness registry search --provides grasp_planning --embodiment franka --installed
```

Each match reports its declared inputs and outputs with schemas, its parameter
ranges, its safety class, its evalcard, and — critically — whether it is
actually launchable in this environment. That last field exists because of what
H1 measured (§9): schema-valid graphs that could not launch were the dominant
failure, and discovery that advertises uninstallable nodes is discovery that
teaches the wrong thing.

**Composition.** The agent writes a dataflow YAML wiring the bridge's
observation topics into a perception node, its pose output into a grasp
planner, the planner's pose into an inverse-kinematics node, and the resulting
`joint_cmd` into the budget guard — whose `joint_cmd_safe` output is what the
bridge actually accepts. The verifier subscribes to `oracle_state`; the rollout
client drives resets and goals.

**Validation.** Before anything runs:

```
harness validate graphs/candidate.yaml
```

Typical first-attempt failures and what the agent learns from each:

| Error | What went wrong | What the hint says |
|---|---|---|
| `INPUT_NO_PRODUCER` | A topic is consumed but nothing publishes it | Which node provides that capability |
| `SCHEMA_MISMATCH` | Arrow types disagree across an edge | The expected type from the closed vocabulary |
| `MOTION_UNGATED` | `joint_cmd` reaches the bridge without traversing the guard | Route it through `budget-guard` |
| `ORACLE_LEAK` | A policy node subscribes to `oracle_state` | Only `verifier-*` nodes may; use the estimated-pose path |
| `INSTALL_MISSING` | A manifest's distribution is not installed | An installed, embodiment-compatible alternative that covers the same `provides` |
| `EVAL_MISSING_FOR_MOTION` | A motion-class node has no evalcard | Register it with measured statistics first |

The validator rejects; it never silently rewires. Composition remains the
agent's job, and the error is the teaching.

**Execution.** With an idea logged and the graph valid:

```
harness rollout --graph graphs/candidate.yaml --tier T1 \
                --episodes 20 --seeds 0..19 --reset teleport
```

The harness gates (environment hash, validation, open idea), instruments the
graph with a trace recorder on every declared endpoint, runs the seeded
episodes, and writes the run directory.

**Diagnosis.** The agent reads what came back:

```
harness traces --run <id> --topic joint_state --episode 3
harness report  --run <id>
```

Failures arrive pre-classified. `never_grasped` points at perception or grasp
pose; `dropped` at gripper control or approach; `wrong_object` at identity — and
`wrong_object` is the one that should stop the agent rather than prompt a
parameter tweak. Because traces are per-endpoint and time-aligned by simulated
time, the agent can localize a failure to a node and a moment rather than
guessing at a monolith.

**Improvement.** The agent changes a parameter, swaps a node, or authors a new
one. If it authors one and measures it, it can register it as an evaluated
skill, at which point the capability becomes discoverable for later tasks —
which is the mechanism H3 tests.

The important structural property: at no point can the agent reach the scene,
the scorer, the reset, or the safety limits. Its action space is the graph, the
parameters, and the code of non-frozen nodes.

---

## 6. Determinism as an engineered property

Reproducibility is not assumed here; it is built, measured, and where it cannot
be achieved, it is *bounded and declared*. This section is the most technically
distinctive part of the system.

### 6.1 Why naive determinism fails

A seeded simulation on a fixed commit should reproduce. In practice it does
not, for three independent reasons:

**GPU floating-point nondeterminism.** On the Metal backend, kernel scheduling
produces last-place-digit differences that compound through contact dynamics.
This is irreducible on the required hardware.

**Startup races.** If a policy can act before the scene reaches its initial
state, the run's trajectory depends on process startup order.

**Wall-clock coupling.** This is the subtle one. If physics advances on a wall
timer while a multi-process policy computes, then the wall latency of an
observation→decision→command path is rounded into a *variable number of
simulated steps*. Two runs of identical code and seeds apply the same command
at different simulated times. A measured pair in this project applied its first
command at 0.61 s versus 0.79 s of simulated time under load — a divergence
that appears before any comparison window has closed.

### 6.2 The layered reproducibility contract

Rather than claim a single reproducibility property that the platform cannot
deliver, the contract declares four layers with different strengths:

| Layer | Property | Enforcement |
|---|---|---|
| **(a)** | Seed-derived artifacts are **bit-identical** | Scene layout, initial poses, target selection |
| **(b)** | Reset timing is **exact** | The first post-reset observation cadence is anchored, not raced |
| **(c)** | Physics agreement within **1e-6** through a 1.0 simulated-second window | Compared numerically after each reset |
| **(d)** | Full-episode outcomes are **statistical** | Reported as rates with denominators, never as "this seed passes" |

Layer (d) is an admission, and it is the honest one. Under irreducible GPU
noise, a fixed seed's *outcome* can flip. Any evaluation design that assumes
otherwise — including a single-seed regression gate — is measuring noise. This
is why the project's live gates assert *mechanisms* (did the barrier hold? did
the guard stop the command?) rather than single-seed success.

### 6.3 Removing the wall-clock channel

Startup races were removed by anchoring the run to a reset: nothing publishes
or acts before the scene is initialized. Control loops that ran on wall timers
were retimed onto simulated-time signals, so their decision counts are a
function of the trajectory rather than the host's load.

The remaining channel — physics advancing on a wall timer while policies
compute — is addressed by a ratified decision to run attested simulations as a
sequence of **closed, stratified turns**. The bridge publishes the observations
due at a simulated state, tags them with a turn identifier, and does not
advance physics until every participating node has declared completion for that
turn and a terminal barrier commits. Watermarks carry per-port emission counts,
so "no command this turn" is a declared zero rather than a timeout guess.

Two details make this workable on a real graph. First, request/reply and action
loops are structural in this system — client↔verifier and guard→state-machine
cycles exist in every real graph and never cross the bridge — so a naive
"wait for all inputs" rule cannot terminate. Reply and verdict edges are
therefore declared **episodic** and deliver into the *next* turn: cycles remain
legal and deterministic, delayed by exactly one turn. Second, nodes must not
emit turn-stamped messages from wall-timer handlers, or the wall clock re-enters
through the back door.

The cost is real — a barrier per simulated step, order thousands of turns per
episode — and it is budgeted explicitly, with the requirement that lockstep
wall cost stay within a bounded factor of free-run or be re-budgeted openly.

### 6.4 Why this matters for the model agenda

A learned policy introduces its own nondeterminism: sampling temperature,
batching, non-deterministic kernels, and inference-server scheduling. A
substrate that has already isolated *orchestration* nondeterminism can attribute
what remains to the model. Without that separation, "the VLA is stochastic" and
"our harness is racy" are indistinguishable. §10 depends on §6.

### 6.4a The turn protocol in detail

Because the lockstep design is the least conventional part of the system, it is
worth stating precisely.

An attested simulation is a sequence of **closed turns**. Let `S(k)` be the
realized scene when turn `k` opens.

1. The bridge publishes every observation due at `S(k)`, then a **watermark**
   carrying the turn identifier. Every message in the turn carries matching turn
   metadata. The watermark enumerates *every* output port of the node with the
   number of messages emitted on each — so "I published nothing on this port"
   is a declared zero rather than an absence that must be timed out.

2. Each participating node consumes turn `k` in causal order. It closes only
   after receiving every count declared by every upstream for that turn, then
   emits its own outputs and its own watermark. Closure therefore never depends
   on transport arrival order, which matters because the runtime makes no
   cross-port ordering guarantee.

3. A terminal barrier emits a single commit once every branch has closed. The
   bridge then applies commands in a canonical port order, advances physics
   exactly once, and opens `S(k+1)`. A reset turn injects state and advances no
   physics.

4. Anything missing, duplicated, stale, or from a previous process incarnation
   is a loud protocol error. A wall watchdog may **abort** a hung dataflow but
   may never advance physics or manufacture a commit — the wall clock can stop
   the run, never influence it.

**Participants are defined topologically**: any node with a forward-edge path
to a bridge command or reset input. Measurement taps — the trace recorder, a
held-out verifier — have no such path and are exempt by construction, so a slow
observer can never stall physics.

**Cycles are stratified rather than forbidden.** Request/reply and action loops
are structural in this system and never cross the bridge, so a rule that waits
on all causal inputs cannot terminate. Reply, verdict, and violation edges are
declared *episodic*: a message emitted on such an edge in turn `k` is consumed
at the opening of turn `k+1`. Its count is known from the producer's turn-`k`
watermark, so consumption stays exact — deterministic, delayed by precisely one
turn. The forward-edge graph must be acyclic; every cycle must contain at least
one episodic edge; the validator checks exactly that.

**Heavy work runs inside its turn.** When a perception-based verifier's judgment
drives the loop, its multi-second evaluation happens *within* the episode-end
turn, with a watchdog budget sized for that work. The alternative — letting the
verdict land in whichever turn the wall clock happens to reach — is the
wall-scheduling the protocol exists to remove.

The honest cost: a barrier per simulated step, on the order of thousands of
turns for a minute-long episode. The decision requires measuring lockstep
against free-run wall cost and either staying within a bounded factor or
re-budgeting openly, because campaign wall ceilings are frozen and an
unbudgeted slowdown spends real ceiling silently.

### 6.5 A catalog of real determinism failures

The layered contract of §6.2 was not designed in the abstract. Each layer
exists because a specific class of nondeterminism was found, diagnosed, and
fixed. The catalog is worth stating, because these failure modes recur in any
multi-process robotics stack:

**Startup race.** A policy acting before the scene reached its initial state
made the first commanded step depend on process launch order. *Fix:* the run is
anchored to a reset — nothing publishes or acts until initialization completes.

**Wall-timer control loops.** Control loops driven by wall timers performed a
host-load-dependent number of iterations per simulated second, so the same
trajectory produced different command counts on a loaded machine. *Fix:* retime
loops onto simulated-time signals, so iteration count is a function of the
trajectory.

**Wall-quantized step assignment.** The residual and subtlest channel: physics
advancing on a wall timer while policies compute rounds decision latency into a
variable number of simulated steps. *Fix:* the lockstep turn protocol of §6.3.

**Latest-wins queues under a slow consumer.** A consumer that reads *the
present* and a consumer that replays a *bounded past window* need opposite
queue policies. Getting this backwards produces reads that are silently tens of
simulated seconds stale — with no error, because every message is individually
valid. This one is worth dwelling on: it was diagnosed only by instrumenting
the live run and comparing the stamp a consumer *used* against the stamp its
request was anchored to.

**Ambiguous zero stamps.** Metadata defaults meant "no stamp" and "stamp at
simulated time zero" were the same value. A staleness check anchored at zero
either never fires or always fires. *Fix:* a total parser that maps absent,
zero, and malformed to a single "no usable clock" value, so consumers make an
explicit decision instead of comparing against a sentinel.

**Frame selection racing the control dialogue.** When a perception read is
requested after a multi-hop request/reply exchange, "the next frame that
arrives" is a wall-clock race. *Fix:* the request carries a simulated-time
barrier, and the reader answers only from a frame strictly newer than it — with
a bounded refusal if no such frame ever arrives, so the tour cannot hang.

Two lessons generalize beyond this repository. First, **nondeterminism hides in
the orchestration, not the algorithm**: every one of these was a scheduling or
transport property, not a numerical one. Second, **the fix is almost always to
make simulated time the only clock that decides anything**, and to treat wall
time as fit only for liveness backstops that abort rather than act.

---

## 7. The evidence architecture

### 7.1 The chain

```text
idea → frozen treatment → validated graph → seeded execution
     → outcomes / traces / costs → integrity audit → scoped claim
```

The chain is fail-closed at every link. A run with unknown code, treatment
drift, incomplete held-out scoring, or a failed post-run audit may still be
useful diagnostic material — but it cannot silently become verdict evidence.

### 7.2 The idea gate

A hypothesis must be logged before the run that tests it, with a parent idea
and an expected effect. This structurally prevents the most common failure of
autonomous research: running many things and narrating the winner afterwards.

### 7.3 Attestation and the frozen set

The frozen set is the task's definition: scene, verifier, reset, robot assets,
expert baselines, safety limits, and campaign budgets. A hash over all of it
rides with every run. Changing any of it requires a human-reviewed
`env-change` pull request and a regenerated hash.

The set exists so that a measured improvement cannot come from quietly making
the task easier, the scorer friendlier, or the envelope wider.

### 7.4 Admissibility and the honest verdict

The most instructive thing this project has produced so far is arguably a
*non-result*. In the H3 campaign, an admissibility audit applied after the
fact flagged every library-arm cell for drift — repository, treatment, or
runtime. The earlier headline verdict of "not met" was not replaced by a
"met"; it was **dissolved**, and the hypothesis returned to PENDING with
`met: null, complete: false`.

That is what a working evidence architecture looks like from the inside. It
retracts your conclusions. A system that only ever confirms is not measuring.

### 7.5 What a record can and cannot support

Many episodes from one graph estimate *that graph's* task performance. They are
not independent replications of a research agent's ability to *discover* the
graph. Independent agent sessions are the research-process replicates. Conflating
the two inflates apparent confidence by orders of magnitude, and the harness
records both units separately so the distinction survives into analysis.

### 7.5a What a run directory contains

Every run produces a self-describing directory. The shape matters, because it
is what an agent — or a reviewer six months later — actually reads:

| Artifact | Contents |
|---|---|
| `manifest.json` | Git SHA, environment hash, graph hash(es), dependency selection, simulation backend and device, tier, seeds, budgets, relaunch count, ledger entry hashes |
| `episodes.jsonl` | One row per episode: index, seed, status, failure class, retries, correlation id |
| `graph.yaml` | The exact instrumented graph that ran, with absolute node paths |
| `traces/*.arrow` | Per-endpoint columnar logs: simulated time, env id, sequence, payload |
| `verifier_stages.jsonl` | Per-stage realistic-verifier evidence, correlated by goal id |
| `*.mp4` | Overhead video |
| `dora.stderr.log` | Runtime diagnostics |

Two properties make this useful rather than merely voluminous. Traces are
**per-endpoint**, not per-topic, because two nodes may legitimately produce the
same topic name and conflating them destroys attribution. And every row is
stamped in **simulated time**, so evidence from different nodes is aligned on
the clock that determines behavior rather than the one that determines
scheduling.

The `report` CLI turns this into metrics; `traces` slices it by episode and
topic. Both emit JSON, so the agent consuming them is not parsing prose.

### 7.6 Engineering lessons that generalize

Several findings from building this system are not specific to it, and are
recorded because they cost real debugging time:

**Queue policy is a function of the consumer's *role*, not of the topic.** The
same camera stream needs a deep queue for a consumer replaying a bounded past
window and a depth-1 latest-wins queue for a slow consumer that reads the
present. Applying one rule uniformly produces silent staleness in one direction
or silent loss in the other. Neither raises an error.

**Trust boundaries need total parsers.** Any value crossing a process boundary —
a timestamp, an environment id, a pose — must be read by a function that
degrades rather than raises. A safety node killed by a malformed integer from an
upstream node is a safety node that has failed open. The corollary: a crash that
*happens* to stop motion is not a safety mechanism, and replacing it with a
graceful degradation can silently remove the only thing that was stopping the
robot. Both directions of that trade have been observed here.

**A test that cannot fail is worse than no test**, because it produces
confidence. Several tests in this project asserted the arithmetic of a fix
while leaving the *wiring* of that fix uncovered — deleting the call site left
the suite green. The discipline that catches this is mutation: change the
implementation, confirm the test goes red. It is cheap and it is the only way
to know a test pins what its docstring claims.

**Constants encode an environment.** A wall-time budget expressed as a
multiplier over simulated time silently encodes the realtime factor of the
machine it was written on. Desk scenes run around 0.5; retail scenes around
0.07 — a twentyfold difference. The same constant that is generous on one is a
guaranteed timeout cascade on the other. Budgets should be derived per
configuration, and unmeasured combinations should refuse rather than return a
number that scores zero.

**Ambient environment is a supply-chain surface.** Configuration read from the
process environment, rather than from an attested graph or an explicit
argument, lets a stale developer shell silently change what a measured run
does — while the git SHA, environment hash, and graph hash all attest clean.
Anything that alters a run's meaning must be scrubbed from the ambient
environment or explicitly set by the runner.

**Identifier namespaces collide under parallel authorship.** When several
agents author decision records concurrently, they claim the same next number.
The fix is to derive the next identifier from the integration branch at
authoring time, not from the branch's own history.

### 7.7 The agent contract

Agents in this system are not prompted ad hoc. Two written contracts define
what they may do, and they are deliberately different documents.

The **development contract** governs agents implementing the substrate. It
mandates a spec-driven loop: restate the requirement IDs you will satisfy,
**write the acceptance tests first** with each citing its IDs, implement until
they pass, run the gates, and open a pull request listing the IDs. It fixes the
environment (one package manager, one lockfile, no bare `pip`), forbids editing
specs outside a labelled pull request, forbids touching the frozen set without
human review, and requires that ambiguity be resolved by recording a decision
record and proceeding rather than by stalling or guessing silently.

The **research contract** governs agents running experiments inside campaigns.
It is a separate artifact on purpose: an agent optimizing a metric needs
different rules from an agent implementing a specification. It carries the
budget semantics, the idea-gate requirement, the failure taxonomy, and
copy-pasteable tool examples — because agents, like students, learn from
examples rather than descriptions.

Both are kept short. Every line an agent must scroll past costs tokens on
every turn, which is a real constraint on a document read thousands of times
in a campaign.

Two design choices in these contracts are worth extracting:

**Tests before implementation, citing requirement IDs.** This is not process
theatre; it is what makes the automated traceability check possible. A tool
scans for MUST-level requirements lacking a citing test and fails the build,
so the identifier system stays honest without human bookkeeping.

**The gates are ordered and mandatory.** Review, simplify, format, lint, unit
tests, and — when simulation or graph code changed — the slower graph tests. An
agent that skips gates produces changes that must be reverted rather than
amended. The ordering matters: review before simplify, because simplifying code
you have not understood is how correctness is lost.

---

## 8. The experimental program

### 8.1 Task tiers

| Tier | Task | Skill under test |
|---|---|---|
| `T0` | Pick a known box at a fixed pose → tray | Environment bring-up |
| `T1` | Pick the *named* medicine among 5 at randomized poses | Grounding + grasping |
| `T2` | Medicine identified by **label text only**, colors permuted | Open-vocabulary / OCR perception |
| `T3` | Target occluded behind another box | Re-arrangement, planning |
| `T4` | Full request loop: confirm back, deliver, handle "that's the wrong one" | HRI + recovery |
| `S1`–`S3` | Retail store suite | Mobility, planogram compliance, longer horizons |
| `P0`–`P4` | Powder transfer and weighing bench | Force control, balances, tool changes |

### 8.2 Perception rungs

How much privileged information the policy may use, enforced statically and at
runtime:

| Rung | Policy may use | Forbidden |
|---|---|---|
| `L0` | Ground-truth object poses | — |
| `L1` | Segmentation + depth | Ground-truth poses |
| `L2` | RGB + depth only | Poses and segmentation |

The rung is declared in the graph so the graph hash attests it. Verifier nodes
retain oracle access at every rung — a verifier that cannot see privileged
state cannot judge.

### 8.3 Ablations

| Id | Contrast | Question |
|---|---|---|
| `A1` | Agent-composed vs. expert graph | Composition tax, or gain? |
| `A2` | Skill library on vs. off | The mechanism behind H3 |
| `A3` | Params-only vs. params + code authorship | How much authorship freedom is needed? |
| `A4` | Claude Code vs. Codex vs. Kimi Code | Agent comparison at fixed budget |
| `A5` | 1 vs. 4 vs. 8 agents on batched envs | Fleet scaling; token super-linearity |
| `A6` | Teleport vs. behavioral reset | What does teleporting hide? |
| `A7` | Oracle vs. realistic verifier driving the loop | Does verifier noise break learning? |

A7 deserves emphasis. Running the loop on a *perception-based* verifier while
holding the oracle out for scoring is the closest simulated analogue to
deployment, where no oracle exists. If learning survives verifier noise, the
approach transfers; if it does not, that is a finding about every
self-improving robot system, not just this one.

### 8.3a Why a pharmacy shelf

The task family was chosen for properties that make it a good instrument, and
the reasoning transfers to anyone designing an agentic-robotics benchmark.

**A safety-critical failure that is not a collision.** Most manipulation
benchmarks fail by dropping or missing. Handing a person the wrong medication
is a failure of *identity*, not of motion — it can occur with a flawless
trajectory. That makes perception genuinely load-bearing and gives the safety
hypothesis a crisp, countable event.

**A cheap difficulty ladder in one scene.** The same shelf supports fixed-pose
picking (`T0`), named retrieval among distractors (`T1`), identification by
printed text with colors permuted so color cannot predict identity (`T2`),
occlusion requiring rearrangement (`T3`), and a dialogue loop with correction
(`T4`). Difficulty rises without rebuilding the environment, so tier-to-tier
comparisons hold the physics fixed.

**Legible failure classes.** `never_grasped`, `dropped`, `wrong_object`,
`collision`, and `timeout` map to distinguishable subsystem faults. An agent
reading `never_grasped` looks at perception and grasp pose; reading `dropped` it
looks at gripper control. Diagnosability is a property of the task design, not
only of the tooling.

**Everyday, inspectable objects.** Boxes on a shelf are trivially
understandable to a reader judging whether a result is meaningful — which is
worth more than it sounds when the audience must assess an autonomous system's
claims.

**A real deployment analogue.** Retail and pharmacy fulfilment are genuine
applications, so the retail suite (`S1`–`S3`) extends the same contract into
mobility and planogram compliance rather than into a toy.

The powder and force-control family (`P0`–`P4`) exists to test the opposite
axis: whether the contract survives a manipulation regime dominated by force
rather than pose, where success is a measured mass rather than an object in a
tray.

### 8.4 Campaign protocol and budget mechanics

A campaign is a sequence of runs under a shared, metered budget, executed by an
agent session against a protocol fixed in advance.

**Budgets are ceilings, not guidance.** Episodes, wall hours, and tokens are
declared in a frozen configuration file. Reservations are taken atomically
*before* a run launches, and settled against actuals afterwards — so an
interrupted run stays charged at its reservation until reconciled, which is the
conservative direction. The ledger is a hash chain: each entry hashes its
predecessor plus its own canonical content, so an edited or removed line breaks
every hash after it, and each run manifest records the hashes of its own
entries for cross-verification.

**A wedged episode cannot consume the campaign.** A per-episode wall clamp kills
an episode that exceeds its tier's budget, records it as a synthetic
`wall_clamp` failure distinguishable from the verifier's simulated-time
`timeout`, and relaunches for the remaining seeds. A pathological graph that
wedges on every episode therefore finishes with a scored zero rather than an
empty measurement window — a failure that is *recorded* rather than a run that
is *lost*.

**Admissibility is decided after the fact, by audit.** A post-run audit compares
the recorded repository state, treatment identity, and runtime against the
protocol's trusted baseline. Cells that drifted are flagged and excluded from
verdicts. This is what dissolved the H3 headline (§7.4), and it is deliberately
retroactive: an audit that can only be run before the fact cannot catch drift
that occurred during execution.

**Held-out scoring is separated from development scoring.** Development runs
inform the agent; held-out runs decide the verdict. A campaign whose held-out
scoring is incomplete cannot produce a verdict regardless of how good its
development numbers look.

---

## 9. What has been measured

Current verdicts live in the [README status table](../README.md#status), which
is canonical. Summarized:

**H1 (composition) — measured, target not met.** Across 40 attempts, 40/40
produced schema-valid graphs, but only 15% (Claude) and 65% (Codex) launched
zero-shot. The gap had a single dominant mechanism: manifests pointing at
uninstalled packages. The response was not to lower the bar but to make the
failure *legible* — the validator now surfaces `INSTALL_MISSING`, and registry
search reports launchability. This is the paradigm case of the validator as a
teaching surface.

**H2 (iteration) — met on one arm.** The Claude arm reached 1.0 pass@1 held
out; the Codex arm 0.875 at N=8, with dev-side evidence of a ≥0.9 system.

**H3 (skill reuse) — PENDING.** See §7.4. Both tiers UNDECIDED after the
admissibility audit.

**H4 (hot swap) — measured at T0.** Phase-randomized, hot-swap median
iteration latency 32.4 s vs. relaunch 41.8 s (ratio 1.29, n=6 per path, zero
infrastructure failures); the mutation mechanism alone is ~1.7× faster.
Extremes overlap and **no significance or equivalence claim is made at n=6**.
The measurement is explicitly labelled unattested. Note also that the
registered H4 control is an equal-budget monolithic-script condition, which
has not yet been run — only hot-swap vs. relaunch has.

**H5 (safety) — holding.** Zero `wrong_object` outcomes in 224/224 episodes
across three campaign runs. H3's records do not extend that denominator,
because inadmissible cells cannot contribute to a safety claim either.

The pattern across all five is the point: thresholds declared in advance,
results reported with their denominators and their caveats, and at least one
verdict actively withdrawn by the project's own audit.

### 9.1 What the failures taught

The negative and partial results have been more informative than the positive
one, which is the usual pattern and worth making explicit.

**H1 taught that discovery is the bottleneck, not generation.** Every agent
produced schema-valid graphs — 40 out of 40. What failed was launchability, and
the cause was uniform: manifests advertising capabilities whose packages were
not installed. The agent had no way to know, because the discovery surface did
not report it. The instructive part is that this is not a model capability
problem at all; it is an *interface* problem, and the fix was to make the
environment legible rather than to prompt the agent harder. A substantial
fraction of apparent agent incompetence is of this kind.

**H3 taught that campaign hygiene is harder than the science.** The skill-reuse
question is well-posed and the campaign ran to completion. It produced no
verdict, because an audit applied afterwards found that repository, treatment,
or runtime state had drifted in every library-arm cell. The scientific question
was never reached. For anyone building autonomous research infrastructure, the
lesson is that the controls must be *mechanized before* the campaign, since a
post-hoc audit can only tell you what you lost.

**H4 taught to state the control you registered.** The registered comparison is
against an equal-budget monolithic-script condition; what was actually measured
was hot-swap versus relaunch. Both are interesting, they are not the same
claim, and the report says so rather than letting the measured number stand in
for the registered one.

**H5's zero is a denominator, not an absolute.** Zero wrong-object outcomes in
224 episodes is meaningful and bounded. It is not "the system cannot deliver the
wrong medicine"; it is "in 224 recorded episodes under these conditions, it did
not". The safety argument in §11 rests on the guard's structure, and the
measurement is corroboration rather than proof.

---

## 10. The model agenda: VLA, world models, and WAMs

**This section is forward-looking. It describes committed design direction.**

AISLE is model-light today by choice. A classical pipeline is a control: it
isolates the engineering question ("can the agent build and improve a system?")
from the policy question ("is this policy good?"). Now that the substrate,
safety structure, determinism contract, and evidence architecture exist, the
learned-policy families become the natural next treatments — and the
architecture was designed from the start to receive them.

### 10.1 The integration principle

VLA policies, world-model planners and environments, and World Action Models
enter as **typed, swappable nodes behind the same action adapters, guard,
verifier, and evidence contract** as every classical node. No new evaluation
path, no new safety path, no new evidence path.

That single sentence is the payoff of the whole architecture. Because the
task, the scorer, the reset, the safety envelope, and the budget are frozen
and hash-attested, a learned policy dropped into the same graph position is
*automatically* a matched comparison. The confounds enumerated in §2 are
already controlled. The comparison "classical pipeline vs. learned policy vs.
predictive hybrid" costs a node swap and a rollout, not a new experimental
apparatus.

### 10.2 The action adapter

The adapter is the contract boundary between a model's native interface and the
robot's typed command topics. It exists because model families disagree about
almost everything except that they eventually produce actions:

| Concern | What the adapter reconciles |
|---|---|
| **Action space** | End-effector deltas, joint targets, or velocity commands → the contract's `joint_cmd` / `gripper_cmd` / `base_cmd` |
| **Rate** | Model inference at 5–30 Hz → the contract's ≤100 Hz control rate, by holding, interpolating, or re-planning |
| **Observation assembly** | Contract topics at their declared rates → the model's expected frame stack, resolution, and normalization |
| **Horizon** | Action chunks → a stream of timed commands with a defined preemption rule |
| **Uncertainty** | Model confidence → an explicit refusal, so low confidence produces a stop rather than a guess |

The adapter is where sim-to-real portability is preserved: it speaks the topic
contract on one side, so the same learned policy runs against simulated and
hardware drivers unchanged.

Critically, the adapter is *downstream* of nothing and *upstream* of the guard.
A learned policy's output is a proposal. The guard remains the authority.

### 10.3 VLA policies as nodes

A VLA node consumes camera and proprioceptive topics and produces commands.
Because the perception rungs already exist, a VLA can be evaluated at L2 —
RGB and depth only — against a classical pipeline at the same rung, on the same
seeds, judged by the same frozen verifier.

The interesting comparisons are not only "which wins":

- **Where does each fail?** The failure taxonomy is shared, so the *shape* of
  failure is comparable. A policy that fails by `never_grasped` and one that
  fails by `wrong_object` are not equally safe, whatever their success rates.
- **What does each cost?** Inference latency, compute, and tokens are recorded
  alongside outcomes.
- **How often does the guard intervene?** A policy that succeeds only because
  the guard repeatedly clamps it is a different system than one that never
  triggers it.

### 10.4 World models: predictor and environment

Two distinct roles, deliberately separated:

**World model as predictor.** A node that forecasts outcomes of candidate
actions, used to rerank or to drive model-predictive control. It sits between
planning and action, and its value is measured as held-out success, sample
efficiency, planning latency, and — importantly — *uncertainty calibration*,
because a confident wrong prediction is worse than an uncertain one.

**World model as environment.** A learned simulator substituted for the
Genesis bridge behind the same topic contract. This is the screening
application: if candidate policies can be ranked cheaply in a neural
environment and the ranking agrees with physics, the cost of search collapses.
The measurement that matters is *rank agreement* plus false-promotion and
false-rejection rates against the physics simulator — and eventually against
hardware.

Because the bridge is a node behind the contract, this substitution is
architecturally the same operation as swapping a driver. The verifier and the
evidence path do not change.

### 10.5 World Action Models

A WAM couples action generation and world prediction in one model. In this
substrate it occupies both node positions at once, which makes its comparison
against a decomposed predictor+policy pair a clean architectural question:
does end-to-end coupling beat modular composition on the same task, budget, and
scorer?

### 10.6 Model provenance is part of the evidence

A model family name is not evidence. A valid comparison in this system records
the checkpoint or revision, preprocessing, precision, inference backend and
device, observation and action contract, decoding parameters, latency, compute
cost, and any model-specific randomness — attached to the node's evalcard, in
exactly the same structure that already records a classical node's measured
performance.

This is the extension of the frozen-set logic to learned components: you cannot
compare two policies whose provenance you cannot state.

### 10.7 A staged integration plan

The agenda is staged so that each step is independently measurable and each
adds one source of uncertainty at a time.

**Stage 1 — the adapter contract as a specification.** Before any model lands,
the action adapter becomes a normative spec with citing tests: observation
assembly, action-space conversion, rate reconciliation, chunk preemption, and
the uncertainty→refusal path. Written first so that every model family that
follows targets the same boundary, and so that a swap is a swap rather than a
port.

**Stage 2 — a VLA policy node at L2, offline-evaluated.** The first learned
node replaces the perception→planning→IK span for a single tier, at the
hardest perception rung, judged by the frozen verifier against the classical
baseline on the same seeds. Offline first: run it in sidecar, comparing its
proposed actions against what the classical stack did, before it drives
anything.

**Stage 3 — the VLA drives the loop.** The same node, now upstream of the
guard, on the same task and scorer. What changes is only which node produces
`joint_cmd`. Guard interventions, failure-class distribution, latency, and
compute are recorded alongside success.

**Stage 4 — world-model predictor.** A node that scores candidate actions,
inserted between planning and action. Measured on held-out success, sample
efficiency, planning latency, and calibration.

**Stage 5 — world-model environment.** The learned simulator substituted for
the Genesis bridge behind the same topic contract, used to rank candidate
policies. Validated by rank agreement against physics before it is trusted for
screening.

**Stage 6 — WAM, and the agent's own choice.** A coupled action/world model
evaluated against the decomposed pair. And the question that motivates the
whole program: does the coding agent, given all of these as registry entries
with evalcards, reach for the right one?

Each stage is a node swap plus a rollout. That is the point.

### 10.8 What the adapter specification must pin

The adapter is where most integration bugs will live, so it is worth stating
what it has to get right — each item corresponds to a failure mode already
observed with classical nodes:

| Concern | Requirement | Failure it prevents |
|---|---|---|
| Observation staleness | Frames assembled for an inference must be provably newer than the state they describe | Acting on a stale view (§6.5) |
| Rate reconciliation | A defined hold/interpolate rule between inference rate and control rate | Command starvation or aliasing |
| Chunk preemption | An explicit rule for what happens to a queued action chunk when a new inference lands | Two policies fighting over the arm |
| Refusal | Low confidence produces a stop, not a guess | Confident wrong actions |
| Determinism declaration | Sampling parameters and seeds recorded; non-deterministic kernels declared | Attributing model noise to the harness |
| Latency accounting | Inference time recorded per decision | Comparing a slow policy to a fast one without saying so |
| Provenance | Checkpoint, precision, backend, device on the evalcard | Comparing two policies whose identity you cannot state |

### 10.9 Evaluation designs, per family

**VLA vs. classical, matched.** Same tier, same seeds, same rung, same frozen
verifier, same guard. Report success, failure-class distribution, guard
intervention rate, latency, and compute. The comparison is fair by construction
because everything except the node under test is hash-frozen.

**Predictor value.** Ablate the predictor from an otherwise identical graph.
The question is not "is the predictor accurate" but "does the system act
better with it", which is a different and more useful measurement.

**Neural environment as a screen.** Rank N candidate policies in the learned
environment and in Genesis. Report rank correlation, false promotions (ranked
high, physics says bad) and false rejections. A screening tool is useful at
much lower fidelity than a scoring tool, and this design measures exactly the
property that matters.

**WAM vs. decomposition.** The coupled model against predictor+policy at equal
budget. This is the architectural question stated as an experiment.

### 10.10 What would falsify the model thesis

The forward-looking claim is that a typed substrate with frozen evaluation
makes learned-policy research *cheaper and more credible*. It would be
falsified if:

- adapters turn out to leak so much model-specific structure that each family
  needs its own bespoke integration, making "swap" a fiction;
- the contract's rate and typing constraints degrade learned policies enough
  that the comparison is unfair to them — a real risk for policies trained on
  different observation stacks;
- the guard's interventions dominate learned-policy behavior so thoroughly that
  what is measured is the guard, not the policy;
- or the determinism contract cannot absorb model nondeterminism, leaving
  results as noisy as they would have been without the substrate.

Each of these is measurable, and each would be worth reporting.

### 10.7 The research questions this opens

| Id | Question | Contrast |
|---|---|---|
| `M1` | When does a learned policy add value? | Classical graph vs. VLA vs. hybrid fallback |
| `M2` | Does predictive planning improve action selection? | Direct policy vs. world-model reranking vs. MPC |
| `M3` | Is a neural environment useful for screening? | Rankings in neural env vs. Genesis, then hardware |
| `M4` | Does representation survive transfer? | Same task contract across rungs and embodiments |

(These share letters with the milestone codes `M0`–`M3`; context disambiguates,
and the [glossary](glossary.md) documents both namespaces.)

The deepest question is the one the substrate makes newly askable: **can the
coding agent itself decide when to reach for a learned component?** An agent
that can compose classical nodes, measure their failure modes, propose a VLA
for the sub-problem where classical perception fails, evaluate it under the
same frozen scorer, and register it as a reusable evaluated skill — that is the
full loop this project exists to test.

---

## 11. Safety under learned policies

Introducing learned policies sharpens the safety question rather than changing
its structure, and the architecture's answer is deliberately unglamorous:
**the guard does not learn.**

- The guard remains a classical, frozen, non-bypassable node. A learned policy
  cannot widen its own envelope, because the envelope is enforced downstream by
  a component in the hash-frozen set and topologically required by the
  validator.
- Guard interventions are recorded topics. "How often did the model propose
  something the guard had to clamp?" is a measured quantity — and it is a far
  better safety signal than success rate, because it counts *near misses* that
  a success-only metric hides.
- The `wrong_object` asymmetry survives unchanged. A learned policy is judged
  by the same frozen verifier, and delivering the wrong medicine remains
  categorically worse than failing to deliver.
- Refusal remains a first-class output. The adapter's uncertainty path means a
  low-confidence model produces a stop, not a guess — the same fail-closed
  principle applied to every parser, stamp, and calibration in the system.

H5 — zero wrong-object outcomes under free iteration — is stated over *agent
iteration*, and it must continue to hold when the thing being iterated is a
learned policy. If it does not, that is among the most important results this
project could produce.

### 11.1 Four safety cases

Stating the argument concretely, for the four ways a learned policy could
plausibly cause harm here:

**Case 1 — the policy commands an unsafe motion.** The guard clamps velocity,
enforces keep-out with an entry check rather than a containment check, and
holds on malformed input. The policy's output is a proposal; the guard's output
is what the bridge accepts. The validator makes that topology mandatory, so the
protection cannot be omitted by a graph the agent writes.

**Case 2 — the policy delivers the wrong medicine.** This is `wrong_object`,
and it is caught by a frozen verifier the agent cannot edit, triggered the
moment any non-target box enters the tray rather than at timeout. It is the
tracked quantity of H5.

**Case 3 — the policy is confidently wrong.** The adapter's uncertainty path
turns low confidence into a stop. This is the one case where a learned
component genuinely adds a failure mode the classical stack lacks, because a
classical pipeline's confidence is usually a margin it can be forced to
declare, while a learned policy's may be poorly calibrated. Calibration is
therefore an explicit measurement in §10.9, not an assumption.

**Case 4 — the agent weakens the safety structure to improve its metric.** The
guard, limits, verifier, scene, and reset are hash-frozen; changing any of them
requires a human-reviewed pull request and invalidates the run attestation. The
agent's action space excludes them by construction rather than by instruction.

The residual risk this argument does *not* cover is a guard that is itself
wrong — a limit set too loose, a keep-out zone mis-specified. That is a human
review responsibility, which is precisely why those files are the ones requiring
human review.

---

## 12. Threats to validity

A report that only argues its own case is not evidence. The honest threats:

**Simulation is not the world.** Every measured result here is in Genesis on
Metal. Contact dynamics, perception noise, actuator behavior, and failure
distributions all differ on hardware. The topic contract is designed so that
hardware drivers are a node swap, and the behavioral reset exists to remove one
specific simulator convenience — but no physical-robot evidence exists yet, and
no claim here should be read as a sim-to-real result.

**Small n.** Several results rest on small samples — H4 at n=6 per path, H2's
held-out arm at N=8. The project labels these rather than inflating them; the
correct reading of "hot-swap median 32.4 s vs. 41.8 s at n=6, extremes
overlapping" is *suggestive*, not established.

**Single environment, few agents.** Results are from two coding agents on one
task family and one machine class. A4 exists to widen the agent axis; nothing
yet widens the hardware axis.

**The substrate is evaluated by its authors.** The people who believe typed
dataflow helps built the typed dataflow and the comparison. H4's registered
control — an equal-budget monolithic-script condition — is the guard against
this, and it has not yet been run. Until it is, the substrate's advantage is
argued rather than demonstrated.

**Agent capability is a moving target.** Model releases change results
underneath the experiment. A finding about "coding agents in August 2026" may
not survive the next model generation, which is an argument for reporting
mechanisms and failure modes rather than only headline rates.

**Determinism is bounded, not absolute.** Layer (d) admits that outcomes are
statistical. Any reader wanting bit-exact episode reproduction will not get it
on this hardware, and designs assuming it are measuring noise.

**The evidence architecture can be gamed by a sufficiently capable agent.** The
controls raise the cost of the seven confounds in §2; they do not make them
impossible against an adversarial optimizer. The idea gate depends on the agent
logging honestly; the audit depends on recording what actually happened. These
are strong controls against *careless* optimization, and untested against
deliberate circumvention.

---

## 13. Positioning

AISLE sits at an intersection that is currently sparsely occupied.

**Versus robot learning benchmarks.** Standard benchmarks fix the environment
and compare policies. AISLE fixes the environment *and the evaluation
machinery*, and compares **engineering processes** — with policy comparison
available as a special case once §10 lands. The frozen set and the attestation
chain are the difference.

**Versus agentic coding benchmarks.** Software-agent benchmarks measure code
that passes tests. Here the artifact controls a physical process with a safety
envelope, irreducible nondeterminism, and outcomes that cannot be unit-tested
into existence. The failure modes are different in kind: an agent cannot
retry its way past a `wrong_object`.

**Versus simulation frameworks.** Genesis, MuJoCo, Isaac and others provide
physics. AISLE provides the layer above: typed capability composition, static
validation, enforced safety topology, attested evidence, and a budgeted
research loop. It consumes a simulator rather than competing with one, which is
why substituting a learned world model for the physics bridge is architecturally
routine.

**Versus autonomous-research systems.** Automated science systems generally
optimize in a space where the evaluator is cheap and trustworthy. Robotics
inverts that: evaluation is expensive, noisy, and safety-critical. The realistic
verifier and the A7 ablation exist precisely to study what happens when the
evaluator itself is imperfect — which is the deployment condition.

### 13.1 Open problems

The problems below are open, consequential, and would benefit from outside
attention. They are the honest answer to "what is hard here".

**Evaluating an evaluator.** The realistic verifier is imperfect by
construction, and A7 asks whether a research loop driven by an imperfect
evaluator still improves the system. This is the deployment condition for every
self-improving robot, and there is little literature on it.

**Determinism under learned components.** The layered contract cleanly
separates orchestration nondeterminism from physics nondeterminism. Adding
model sampling, batching, and non-deterministic kernels introduces a third
source that the current layers do not name. What should layer (e) say?

**Credit assignment across the outer loop.** When an agent makes twelve changes
and performance improves, which change mattered? Traces localize *failures* to
nodes; they do not yet attribute *improvements* to edits.

**Skill-library semantics.** An evalcard says a skill worked on a suite. It does
not say when reuse is *appropriate*. H3 assumes accumulated skills transfer;
what governs whether a skill should be reached for at all is unresolved.

**Fair comparison across observation stacks.** A VLA trained on a different
camera configuration is disadvantaged by a fixed topic contract. Making the
comparison fair without making it meaningless is a genuine methodological
problem, and it is the sharpest risk to the model agenda in §10.10.

**The adversarial case.** The evidence controls are strong against careless
optimization and untested against deliberate circumvention by a capable agent.
What does an evidence architecture look like when the subject is trying to pass?

---

## 14. Roadmap

**Delivered.** The typed contract and validator; the Genesis bridge with mobile
and retail extensions; the capability registry with evaluated-skill
registration; the budget guard with keep-out, arm/base mutex, and watchdogs;
oracle and realistic verifiers with a fidelity metric; teleport and behavioral
reset; the rollout harness with Arrow traces, attestation, a tamper-evident
budget ledger, per-episode wall clamping and relaunch, and hot-swap; fleet mode
for batched environments; the layered determinism contract with startup races
and wall-timer control loops removed; H1, H2, H4, and H5 measured.

**In flight.**

| Work | State |
|---|---|
| Deterministic lockstep turns | Protocol ratified; implementation epoch pending, requiring one coordinated change across every measured graph |
| T4 dialogue tier | Contract ratified; implementation next |
| H3 re-run | Blocked on campaign-hygiene fixes so the audit cannot dissolve it again |
| Retail suite hardening | Ongoing |

**Next, in dependency order.**

1. **The action adapter specification** — normative, with citing tests. Nothing
   in the model agenda should land before the boundary it targets is fixed.
2. **A VLA policy node at L2**, sidecar first, then driving the loop, against
   the classical baseline on identical seeds and scorer.
3. **World-model predictor** as a reranking node; measure whether the *system*
   acts better, not merely whether the model predicts well.
4. **World-model environment** behind the topic contract, validated by rank
   agreement against physics before it is trusted to screen.
5. **WAM** against the decomposed predictor+policy pair at equal budget.
6. **Hardware bring-up** through the same contract — the swap the architecture
   was designed for, and the only thing that converts simulated results into
   claims about robots.
7. **The powder and force-control bench family**, which adds force sensing,
   balances, and tool changes — a qualitatively different manipulation regime
   that tests whether the contract generalizes beyond pick-and-place.

**How to engage.** The repository runs on a MacBook: `uv sync --extra sim`,
then `uv run harness validate graphs/expert_t0.yaml` and
`dora run graphs/expert_t0.yaml --uv`. The specs are normative and numbered,
the decisions are recorded with their rejected alternatives, and the notation
is documented (Appendix A). The most useful contributions right now are the
action adapter specification, a VLA node targeting it, and independent
replication of any measured result — especially a failed one.

## 15. Why this matters

Three audiences, three reasons.

**For robot learning research.** AISLE offers a place to evaluate learned
policies where the surrounding engineering is controlled: frozen task, frozen
scorer, enforced perception rungs, recorded safety interventions, attested
environments, and a determinism contract that separates orchestration noise
from model noise. Comparisons cost a node swap.

**For agentic-AI research.** The autonomous engineering loop is the object of
study, with the confounds that usually make such claims unfalsifiable turned
into mechanisms. The project has already demonstrated the discipline working
against its own interests, retracting a verdict on audit.

**For robotics practitioners.** The typed-dataflow substrate, the validator's
teaching errors, the non-bypassable guard, and the sim→real node swap are
reusable engineering patterns independent of whether the research hypotheses
hold. And it runs on a laptop.

The project is ongoing. The model-light present is a control condition, not a
destination; the substrate was built to receive the learned components that
come next, and the interesting experiments start when they arrive.

---

## Appendix A: notation

Full expansions in [`glossary.md`](glossary.md). Quick reference:

| Code | Meaning |
|---|---|
| `CON`, `TC`, `SCN`, `BRG`, `VER`, `RST`, `CAP`, `VAL`, `HAR`, `BG`, `MOB`, `RS`, `PW`, `FT`, `BAL`, `TOOL` | Requirement prefixes, one per specification (constitution, topic contract, scene, bridge, verifier, reset, capability, validator, harness, budget guard, mobility, retail scenarios, powder, force/torque, balance, tool changer) |
| `ADR-N` | Architecture Decision Record |
| `DoD` | Definition of Done — a phase's exit criteria |
| Class A / B / C | Change-risk classes; Class C (frozen set, contract changes) requires human review |
| `H1`–`H5` | Hypotheses |
| `A1`–`A7` | Ablations |
| `M0`–`M3` | Milestones and phases; also `M1`–`M4` as model-research questions in §10.7 |
| `T0`–`T4`, `S1`–`S3`, `P0`–`P4` | Task tiers (desk, retail, powder) |
| `L0`–`L2` | Perception rungs |
| `L/`, `W/` | Campaign arms: library-persisted, library-wiped |
| pass@1, pass@8 | First-attempt success; success within ≤8 **in-context** retries within one episode |
| rtf | Realtime factor — simulated seconds per wall second |
| VLM, VLA, WAM | Vision-Language Model; Vision-Language-Action model; World Action Model |

## Appendix B: reading path

| To understand | Read |
|---|---|
| What and why, current status | [`../README.md`](../README.md) |
| Field concepts (Physical AI, VLM/VLA/world models, sim-to-real) | [`physical-ai-primer.md`](physical-ai-primer.md) |
| Notation and acronyms | [`glossary.md`](glossary.md) |
| Research framing and claim discipline | [`research-program.md`](research-program.md) |
| Original experiment design | [`Project_AISLE_Experiment_Design.md`](Project_AISLE_Experiment_Design.md) |
| Source-linked project map | [`contributor-wiki.md`](contributor-wiki.md) |
| Normative invariants | [`../specs/000-constitution.md`](../specs/000-constitution.md) |
| Runtime wire contract | [`../specs/010-topic-contract.md`](../specs/010-topic-contract.md) |
| Decision history | [`decisions/`](decisions/) |
| Experiment protocols and evidence | [`experiments.md`](experiments.md) |
| Getting it running | [`getting-started.md`](getting-started.md) |
