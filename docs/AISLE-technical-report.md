# AISLE — A Substrate for Autonomous Robot Engineering

**Agentic Infrastructure for Safe Learning and Execution · Technical report · August 2026**

*Status snapshot: 2026-08-16, with **Phase 2 and Phase 3 both closed**.
Measured results cite the [README status table](../README.md#status), which is
canonical; the phase record is
[`analysis/reports/phase2_phase3_report.md`](../analysis/reports/phase2_phase3_report.md).
Forward-looking sections are labelled as such and describe committed design
direction, not shipped capability.*

---

## Contents

0. [How to read this report](#0-how-to-read-this-report)
1. [Executive summary](#1-executive-summary)
2. [The problem: why "the demo worked" is not evidence](#2-the-problem-why-the-demo-worked-is-not-evidence)
3. [The research question](#3-the-research-question)
   - [3.5 What "learning" means in AISLE](#35-what-learning-means-in-aisle)
4. [The architectural bet: typed dataflow](#4-the-architectural-bet-typed-dataflow)
5. [System architecture](#5-system-architecture)
   - [5.5 Anatomy of a skill](#55-anatomy-of-a-skill)
   - [5.6 What actually happens when you run a rollout](#56-what-actually-happens-when-you-run-a-rollout)
6. [Determinism as an engineered property](#6-determinism-as-an-engineered-property)
7. [The evidence architecture](#7-the-evidence-architecture)
   - [7.5 Units of measurement: episode, rollout, attempt](#75-units-of-measurement-episode-rollout-attempt)
   - [7.6 The scorer is not a reward function](#76-the-scorer-is-not-a-reward-function)
8. [The experimental program](#8-the-experimental-program)
   - [8.5 The idea loop, worked end to end](#85-the-idea-loop-worked-end-to-end)
   - [8.6 Running an autonomous campaign with integrity](#86-running-an-autonomous-campaign-with-integrity)
9. [What has been measured](#9-what-has-been-measured)
   - [9.05 How to read the evidence](#905-how-to-read-the-evidence-a-skill-worth-acquiring)
10. [The model agenda: VLA, world models, and WAMs](#10-the-model-agenda-vla-world-models-and-wams)
11. [Safety under learned policies](#11-safety-under-learned-policies)
12. [Threats to validity](#12-threats-to-validity)
13. [Positioning](#13-positioning)
14. [Roadmap](#14-roadmap)
15. [Why this matters](#15-why-this-matters)
16. [Appendix A: notation](#appendix-a-notation)
17. [Appendix B: reading path](#appendix-b-reading-path)
18. [Appendix C: exercises and hackathon tracks](#appendix-c-exercises-and-hackathon-tracks)
19. [Appendix D: open gaps, with issue numbers](#appendix-d-open-gaps-with-issue-numbers)
20. [Appendix E: common misconceptions](#appendix-e-common-misconceptions)
21. [Appendix F: recurring design patterns](#appendix-f-recurring-design-patterns)
22. [Appendix G: the failure taxonomy, and how to diagnose each class](#appendix-g-the-failure-taxonomy-and-how-to-diagnose-each-class)

---

## 0. How to read this report

This report doubles as the project's teaching text. It assumes you can read
Python and have seen a robot arm in a simulator, and it assumes **nothing**
about dataflow runtimes, robot learning, or experiment methodology. Where a
term is load-bearing it is defined at first use, and
[`docs/glossary.md`](glossary.md) expands every identifier (`CON-5`, `VAL-6`,
`H3`, `T2`, `L1`) and names the file that defines it.

### Three reading paths

**If you are new and want to understand the system** (≈2 hours): §1 → §2 →
§3.5 (what "learning" means here, which is not what the name suggests) → §4 →
§5 → §5.5 (a skill, concretely) → §9. Skip §6 and §7 on a first pass; come
back when something surprises you.

**If you are here for a hackathon and want to build** (≈45 minutes, then
code): §1 → §5 → §5.5 → §7.5 (the three units — get this wrong and your
measurement means nothing) → Appendix C for tracks, Appendix D for open gaps
you could close.

**If you are reviewing the science**: §7 → §7.5 → §7.6 → §8 → §8.5 → §9 →
§12. The claim discipline is the contribution; the robot task is the
instrument.

### The one idea to carry through

Everything here follows from a single refusal: **a working demo is not
evidence.** A robot that completes a task tells you almost nothing, because
you cannot tell from the outcome whether the system is good, the seed was
kind, the scorer was lenient, or the code changed underneath the measurement.
Every mechanism in this report — the frozen scorer, the typed graph, the
attestation hash, the idea gate, the held-out seeds — exists to make one
specific alternative explanation impossible. When a mechanism seems fussy,
ask which alternative explanation it kills; that is always the answer.

### A note on how the results read

Several headline numbers here are negative, undecided, or withdrawn. That is
not modesty. A project whose stated contribution is evidence discipline has to
demonstrate the discipline on its own results first, and the most instructive
sections of this report (§9.1, §9.2) are the ones where the project's own
audit dissolved a verdict it wanted.

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

### 2.5 Six alternative explanations, and the mechanism that kills each

§0 promised that every mechanism in this system exists to make one specific
alternative explanation impossible. This section makes that concrete, and it
is the fastest way to hold the whole architecture in your head at once.

Suppose an agent reports: *"I improved the system from 40% to 90% success."*
Before believing the claim, list the ways it could be true-sounding and wrong.

---

**Explanation 1 — "The seeds were kind."** The agent tuned until it happened to
work on the episodes it could see.

> **Killed by:** the dev/held-out split. Seeds `0..49` are the agent's; seeds
> `100..107` are scored by the runner *after* the session ends, on the system
> as left. The ranges are validated disjoint before the campaign starts, and
> "dev-set numbers never headline" is enforced editorially. §7.6.

---

**Explanation 2 — "The scorer got easier."** The agent adjusted, directly or
indirectly, what counts as success.

> **Killed by:** the frozen set. The verifier, reset, scene, guard, and the
> measured graphs are hash-manifested, and the rollout runner refuses to start
> if the hash differs from the trusted baseline. The agent can *read* the code
> that judges it and cannot change it. §4, §5.6.

---

**Explanation 3 — "The environment drifted."** Something changed underneath —
a dependency, a physics parameter, the runtime — and the improvement is an
artifact of the change.

> **Killed by:** attestation. Every run records an environment fingerprint and
> hash covering the frozen set *and* the selected simulator extra; campaigns
> additionally record the content identity of the dora CLI, because a version
> mismatch once invalidated a scenario and neither `--version` nor a committed
> hash could see it. Runs that cannot attest are labelled UNATTESTED and make
> no reproducibility claim. §6, §9.05.

---

**Explanation 4 — "The agent cheated."** It read privileged state, bypassed the
safety guard, or ran code the registry never vetted.

> **Killed by:** structural checks rather than trust. `oracle_state` cannot be
> routed to a non-verifier node (VAL-6). Every motion path must traverse the
> guard (VAL-5). A graph node whose `path` does not resolve to its manifest's
> `source` is refused, because dora launches the path — a real incident, caught
> live. These are compile-time properties of the graph, not behavioural
> expectations of the agent. §4.

---

**Explanation 5 — "It was more compute, not a better idea."** The improvement
came from spending more, and would have arrived anyway.

> **Killed by:** budgets enforced from outside and recorded per idea. Token
> ceilings are counted from the live stream by the runner, not from a log the
> session could rewrite; wall ceilings likewise. Every campaign record carries
> tokens-to-first-success alongside the outcome, so "better" and "more
> expensive" are separable — which is exactly what let A3 report that the
> *constrained* arm won at half the tokens. §8.6.

---

**Explanation 6 — "The story was written after the data."** The hypothesis was
chosen to fit what happened.

> **Killed by:** the idea gate. A rollout refuses to run unless the branch has
> an open idea, and the idea carries its expected effect and a `git_sha`
> stamped at open time. A6's whole value is that the pre-registered mechanism
> and the observed mechanism *differed*, which is only visible because the
> expectation was recorded first. §8.5.

---

#### What this framing buys you

Two things. First, when a mechanism seems fussy, you can now ask the diagnostic
question — *which explanation does this kill?* — and if there is no answer, the
mechanism is probably ceremony.

Second, it tells you where the system is still weak. There is a seventh
explanation this architecture does **not** kill:

**Explanation 7 — "A capable agent deliberately gamed the controls."** Every
mechanism above is designed against carelessness, ambiguity, and drift. §12
states plainly that the controls are untested against an agent actively trying
to pass, and Appendix C invites you to attack them. Three defects were found by
ordinary review in a single governance pass (§9.2); nobody has yet tried in
earnest.

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
| **H6** | An agent can *operate* a running system: detect an induced degradation, localize it, hot-swap a fix, and recover | The agent cannot localize faults from live evidence alone, or recovery requires reaching outside its sanctioned action space |

**H5 is the safety hypothesis and is stated as a quantity that must remain
zero.** It is reported with an explicit denominator rather than a percentage,
because "99.5% safe" is not a meaningful claim about a system that hands
medication to a person. `wrong_object` — delivering the wrong medicine — is
the failure class the entire perception and verification stack exists to
prevent, and the one the task's design makes ten times worse than a timeout.

**H6 is registered but not yet run**, and it is the newest of the six. H1–H4
ask whether an agent can *build* a robot system; H6 asks whether it can *keep
one running* — monitoring a live dataflow, diagnosing a degradation from traces
and guard evidence, proposing a validated hot-swap, and recovering, with no
human in the loop and no bypass of the safety structure. That is the deployment
condition, and it is why the project's name now reads *Execution*. It needs a
fault-injection protocol and a decision record before a campaign, but it runs
almost entirely on machinery that already exists (§5.8, §5.9, §7).

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

### 3.5 What "learning" means in AISLE

The project is *Agentic Infrastructure for **Safe Learning** and Execution*,
and this is the word most likely to mislead you. If you arrive expecting a
training loop, you will look for it and not find one. There is **no training
code in the repository** — no `.backward()`, no optimizer, no `def train(`
anywhere in `src/` or `tools/`.

That is not an omission. Four different things wear the word here, and only
the last is the conventional one.

#### Sense 1 — learning as outer-loop search (the primary one)

A coding agent improves the system by composing a graph, validating it,
running episodes, reading traces, and revising. No gradients are involved; the
search operator is an LLM editing YAML and Python.

What makes this *learning* rather than flailing is that the environment
supplies a signal shaped for a reader. The repository names it in exactly
those words, twice: the validator exists *"because its error messages are the
research agent's learning signal."*

The paradigm case is H1 (§9). Agents produced schema-valid graphs on 40 of 40
attempts, but only 15% (Claude) and 65% (Codex) launched. The cause was
uniform — manifests advertising nodes whose packages were not installed — and
**the agent had no way to know.** The fix was not a better prompt. It was
`INSTALL_MISSING`: make the environment legible, and the same agent stops
failing. A large fraction of apparent agent incompetence is of this kind, and
it is an *interface* property, not a model property.

> **For students:** this is the most transferable idea in the project. When an
> agent fails at your task, the first question is not "which model is
> smarter?" but "could any agent have known that from what I showed it?"

#### Sense 2 — learning as accumulation

Skills that outlive the session that produced them: the evalcarded library
(§5.5), and hypothesis H3. This is where "learning" means *compounding*, and
it is the sense AISLE tried hardest to measure and could not decide (§9).

#### Sense 3 — learning in the reinforcement-learning sense

The README calls AISLE *"an environment in the reinforcement-learning
sense."* Read that as a statement about **shape**, not activity: episode
boundaries, seeded resets, dynamics, and a scorer the actor cannot edit. It is
an environment a learner could be plugged into. AISLE does not learn here —
it is the thing you learn *against*. §7.6 works through why the frozen
verifier is not a reward function, which is a distinction worth having
crisply.

#### Sense 4 — learned models

VLA policies, VLM verifiers, world-model environments (§10). Live since the
SmolVLA bring-up, and **inference-only** so far: a pretrained checkpoint
loaded under `torch.no_grad()`, weights pinned by revision hash. The learning
that produced those weights happened elsewhere.

#### What "Safe" modifies

Not an aspiration that learning be conducted carefully. It is structural:
whatever is learning — agent, library, or policy — **the things that judge and
constrain it are frozen and unbypassable.**

| mechanism | what it denies the learner |
|---|---|
| frozen verifier + reset (CON-7) | editing its own scorer |
| oracle isolation (VAL-6) | seeing privileged state |
| motion gating (VAL-5) | commanding the arm unclamped |
| registry floor (ADR-37) | setting its own passing grade |
| held-out seeds | tuning on the test set |

The last two are learning-specific in a way the others are not. ADR-37 in
particular encodes a rule worth stating on its own: **a learner may not
self-certify what it accumulates.** Every governance defect this project found
(§9.2) was a failure of that rule rather than a failure of the learning.

#### The operating definition

> **Learning in AISLE is a change to the system that is recorded,
> attributable, and re-runnable.**

The corollary is the sharp part. An improvement that leaves no such artifact
**does not count as learning here**, because it is indistinguishable from a
lucky seed. This is why learning is externalized into artifacts — a diff on a
typed graph, an evalcarded skill, an idea-tree entry — rather than held in
weights or in a context window. It is also why agent sessions start fresh:
anything that survives a session had to become an artifact to do so, which is
precisely what makes H3's wiped-versus-library comparison meaningful.

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

### 5.5 Anatomy of a skill

A "skill" is the unit of accumulated learning (§3.5, sense 2). It is worth
dissecting a real one, because the design is almost entirely about *what the
author is not allowed to say*.

`ik-transfer-v2` is a trajectory node an agent wrote during the A3 campaign
after a measured collision. On disk:

```
skills/ik-transfer-v2/
├── skill.yaml          # the manifest, agent-authored, eval: null
├── ik_transfer_v2.py   # the node code
└── eval.yaml           # the skill SHIPS ITS OWN EXAM
registry/manifests/ik-transfer-v2.yaml   # written by registration
```

#### The manifest: a typed interface, not a description

The registry manifest is what a *later* agent reads to decide whether it can
wire this node without opening the code:

```yaml
id: ik-transfer-v2
provides: [trajectory_generation]        # the discovery key
requires: [grasp_planning]
inputs:
  grasp_pose:  {schema: pose7d_f32, rate_hz: 30}
  joint_state: {schema: jointvec_f32, rate_hz: 100}
  turn:        {schema: sim_turn_u64, rate_hz: 100, is_clock: true}
outputs:
  joint_cmd:   {schema: jointvec_f32, latency_class: soft_rt}
  turn_done:   {schema: sim_turn_u64, latency_class: hard_rt}
params:
  max_joint_vel_rad_s: {type: float, default: 1.0, range: [0.1, 2.0]}
embodiment: {arm: [franka, so101], gripper: parallel}
safety_class: motion
eval: {suite: t1-l1-routed-transfer, pass_rate: 1.0, last_run: '2026-08-16'}
origin: agent-authored
source: skills/ik-transfer-v2/ik_transfer_v2.py
```

Every field earns its place by enabling a machine check:

- **`provides` / `requires`** drive discovery (`registry search --provides`).
- **`inputs` / `outputs` schemas** let the validator type-check every edge
  before anything runs. `pose7d_f32` is a closed vocabulary entry (CAP-2)
  mapping to a concrete Arrow type and shape.
- **`is_clock: true`** declares participation in the ADR-30 simulated-time
  turn, so the scheduler can verify the graph closes each turn.
- **`params` with ranges** bound what an agent may tune without editing code —
  and A3 showed that this axis alone is often enough (§9).
- **`safety_class: motion`** gates what it may command; motion nodes without
  an evalcard are refused outright.
- **`source`** is checked against the graph node's `path`
  (`PATH_MANIFEST_MISMATCH`), so approved code cannot be swapped under a
  vetted id — a real incident, observed live in an H3 campaign.

#### The five lines that are the whole governance model

Diff what the agent ships against what the registry installs:

```diff
-  "eval": null,
+  "eval": {"suite": "t1-l1-routed-transfer", "pass_rate": 1.0, "last_run": "2026-08-16"},
```

That is the *entire* difference. The agent declares everything about its
skill **except how well it works**. The evalcard is the one field it cannot
write: `harness skill register` runs the shipped `eval.yaml`, measures the
rate, and either fills the field in or rolls the registry back byte-for-byte.

Registration is a staged transaction, and the ordering matters:

1. install the candidate manifest with a clearly-labelled *provisional*
   evalcard — so the skill's own eval graph can discover it instead of failing
   `MANIFEST_MISSING`
2. lint the whole registry
3. run the shipped eval against the staged candidate
4. compare to the floor, then write the real evalcard
5. any failure at any step → exact rollback

#### The exam ships with the candidate — and is frozen

`eval.yaml` names the graph, tier, seeds, episode count, and threshold:

```yaml
suite: t1-l1-routed-transfer
graph: graphs/eval_ik_transfer_v2.yaml
seeds: 30,31,32,33,34,35,36,37
episodes: 8
min_pass_rate: 0.75
```

Note the seed population. Seed 33 is where the agent *found* the collision;
30–32 and 34–37 are its unmodified neighbours, included as a regression guard.
An agent choosing a population that could catch its own fix breaking something
else is the behaviour the idea discipline is meant to produce.

Two protections apply here and both were added after failures:

- The eval **graph** is frozen (ADR-36). It used to be editable by the
  candidate — a gate the examinee can rewrite is not a gate.
- The eval **threshold** is floored (ADR-37). It used to be whatever the
  candidate declared — a skill shipping `min_pass_rate: 0.0` registered at 0.0
  and the gate reported success.

Same failure shape, one layer apart: first the exam paper, then the passing
grade. §9.2 tells that story.

#### How reuse actually happens

A later agent searching for the capability sees this:

```
ik-trajectory    origin=hub             eval.pass_rate=1.0   launchable=True
ik-transfer-v2   origin=agent-authored  eval.pass_rate=1.0   launchable=True
```

The library grows by adding *competing entries under the same capability key*.
Reuse is therefore a search result, not an instruction — and `launchable` is
present because H1's dominant failure was advertising nodes that could not
start.

Reuse is then **measured, not self-reported**. The campaign runner intersects
the scored deliverable's node ids with skills registered in an earlier
scenario:

```python
return sorted(node_ids & prior_skill_ids)
```

That is how the one solid transfer datum was established: an H3 desk
deliverable contained `s3-driver-v1`, a skill registered during the *retail*
suite — a cross-suite transfer the H3 design hoped for and got.

#### The honest limit (open gap #264)

The evalcard says a skill *worked on a suite*. Nothing in the schema says
**when reaching for it is appropriate**. The search result above gives an
agent two entries, same capability, same headline number, and no basis to
choose. Nothing records that `ik-transfer-v2` matters only when the transfer
path sweeps a shelf at the carried box's level.

As a library grows, undifferentiated same-capability entries make selection
*harder*, so the accumulation benefit can invert. Candidate fields
(`applies_when`, `eval.population`, `specializes`, `known_limits`) are
discussed in issue #264; each has the problem that agent-authored free text is
unverifiable, and a field the validator cannot check is documentation.

### 5.6 What actually happens when you run a rollout

The gates described across §4–§7 are abstractions until you watch them fire in
order. This is the real sequence in `run_gates`, and the ordering is deliberate
throughout: **each gate is placed so that the cheapest refusal happens first,
and so that no gate can be satisfied by something a later gate would reject.**

```
uv run harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1
```

**1. Baseline selector is well-formed.** `--env-baseline` must be
`origin/main`, `local`, or a 40-hex commit. A typo here would otherwise
silently produce an unattested run that *looks* attested.

**2. Simulator identity resolves.** Which sim extra is selected, and is it
installed? Declared explicitly so the attestation covers *this* environment
shape rather than a nominal one.

**3. Environment hash check.** `tools/env_hash.py --check` recomputes the hash
over the frozen set and compares it to the trusted baseline. **This is the
no-cheating gate.** If the scene, verifier, reset, guard, or a frozen graph has
drifted from the baseline commit, the rollout refuses to start. Note it runs
*before* anything expensive: you cannot burn an hour of episodes and then
discover the environment moved.

**4. Budget ledger.** How many episodes and how much wall time remain? The
ledger is tamper-evident and enforced from outside the agent's reach.

**5. Validation — against the embodiment that will actually run.** The comment
in the source explains the placement better than prose could: *"a graph whose
nodes do not support it must refuse HERE, not crash hours into the rollout."*
This is where `SCHEMA_MISMATCH`, `ORACLE_LEAK`, `MOTION_UNGATED`,
`INSTALL_MISSING`, `CLOCK_*` and the rest fire.

**6. The idea gate.** Does the current branch have an open idea? If not, refuse
(§8.5). `--no-idea-gate` skips it and is recorded in the run manifest, so the
opt-out leaves a trace.

Only then does an episode start. Inside each episode:

```
reset (seeded) → episode_goal published → the graph runs under
ADR-30 simulated-time turns → verifier judges → episode_result
```

and every topic is recorded to Arrow, with the run manifest capturing the
graph hash, environment hash and fingerprint, seeds, tier, perception rung,
verifier choice, reset mode, budget reservation, and the post-run audit.

> **Why this ordering repays study.** Notice that validation comes *after* the
> hash check. If it came first, an agent could pass validation with an edited
> verifier and only then be told the environment drifted — which is a worse
> error message and a worse audit trail. Nearly every gate ordering in this
> system has a similar argument behind it, and reconstructing those arguments
> is the fastest way to understand the design.

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
| `dora.stderr.log`, `dora.stderr.relaunch-N.log` | Runtime diagnostics, one file per launch |

Three properties make this useful rather than merely voluminous. Traces are
**per-endpoint**, not per-topic, because two nodes may legitimately produce the
same topic name and conflating them destroys attribution. Every row is
stamped in **simulated time**, so evidence from different nodes is aligned on
the clock that determines behavior rather than the one that determines
scheduling. And a run that hits the per-episode wall clamp relaunches, which
splits these artifacts into two kinds. Writers that **truncate** — the traces, the
instrumented graph, the video, the dora stderr log — are scoped **per launch**
(`traces/relaunch-N/`, `graph-rN.yaml`, `dora.stderr.relaunch-N.log`), because
a shared path would let the relaunch erase the evidence of the launch that
wedged, which is precisely the launch under investigation. Writers that
**append** — `episodes.jsonl` and `verifier_stages.jsonl` — stay **per run**
on purpose: a relaunched client continues the run-global episode numbering, so
one file per run is what keeps correlation ids unique and the two files
joinable. `manifest.json` is written once, after the last launch.

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

### 7.5 Units of measurement: episode, rollout, attempt

This is the single most common source of confusion about the results, and
getting it wrong invalidates a measurement without producing any error. Three
units are nested, and they answer different questions:

| unit | what it is | what MORE of it buys you |
|---|---|---|
| **episode** | one seeded run of one graph, from reset to `episode_result` | a tighter estimate of *that graph's* task performance |
| **rollout** | N episodes of one graph over a seed list | a pass@1 for that graph |
| **attempt** | one *fresh agent session* | an estimate of *the agent's ability to produce a working graph* |

The rule the project enforces on itself:

> Many episodes from one graph estimate that graph's task performance. They
> are **not** independent replications of a research agent's ability to
> discover the graph. Independent agent sessions are the research-process
> replicates.

#### Worked example: why H1 ran 40 attempts and only 8 episodes each

A natural objection to H1 goes: *the dominant failure was an uninstalled
package — that is deterministic infrastructure, so a couple of runs should
settle it.* The instinct is right about episodes and wrong about the unit.

H1 ran **20 attempts per agent** (two agents, 40 total), **8 episodes each**,
and the agent was explicitly forbidden from rolling out at all — the H1 prompt
says *"Do NOT run rollouts."* The runner scores whatever graph the attempt
produced. So the episodes are not measuring the agent; 8 is enough because
scoring a fixed graph is the easy part.

The 40 attempts are doing three things episodes cannot:

1. **The estimand is a rate against a pre-registered threshold.** H1 asks
   whether ≥80% of attempts produce a valid, launching dataflow zero-shot. A
   rate needs a denominator, and the result — 15% versus 65% between arms — is
   a between-agent contrast that three attempts cannot separate.
2. **The concentration is the finding.** One attempt shows you *a* failure. It
   cannot show that **24 of 40** attempts died on the same mechanism, and that
   number is what justified the fix: it said surfacing `INSTALL_MISSING`
   converts most of the loss rather than a few percent of it.
3. **The variance lives in the agent.** Each individual failure is
   deterministic — the package is installed or it is not. But *which graph you
   get* is stochastic: same prompt, different composition every session. The
   object under study is stochastic even though every constituent failure is
   deterministic and diagnosable at a glance.

> **The analogy:** you are measuring a compiler's error messages by how often
> programmers reach a working build. Each compile error is deterministic. You
> still need many programmers to learn *which* error dominates — and that is
> the only number that tells you what to fix.

#### The honest counterpart

Most later ablations did **not** get 20 attempts. A3, A4 and A5 are **n=1 per
arm**, and every one is labelled a lower bound rather than a comparison. When
you read "params-only won on efficiency", the correct mental footnote is *one
session versus one session*. That is a real weakness of the results, and it is
the same weakness this section explains how to detect.

### 7.6 The scorer is not a reward function

Because AISLE is described as an environment in the RL sense, readers reach
for RL intuitions. Most of them do not apply, and the differences are
instructive.

#### What the verifier actually emits

Not a scalar. `episode_result` carries a **status enum** — `success`, `fail`,
`running` — plus a **failure class** from a closed taxonomy: `wrong_object`,
`never_grasped`, `dropped`, `collision`, `timeout`. TC-8 makes the oracle's
`status == "success"` the ground truth.

That is a **label with a diagnosis attached**, not a return to maximize.
Nothing optimizes against it: no gradients, no value function, no policy
parameters anywhere in the loop.

The clearest tell is the asymmetric penalty. The task definition says a wrong
medicine is **10× worse** than a failure to deliver — and that 10× lives in
the goal text handed to the agent *as English*, not as a coefficient in a
scoring function. In an RL system it would be a weight. Here it is an
instruction to a reader.

#### The better analogy: held-out model selection

Dev seeds `0..49` are the agent's to probe. Seeds `100..107` are withheld and
scored by the runner **after** the session ends, with the system as the agent
left it. That is a train/test split with a frozen grader, and "dev-set numbers
never headline" is the rule that enforces it.

So the closest classical framing is not reinforcement learning at all — it is
**model selection on a held-out set, where the search operator happens to be a
language model.**

#### Why a taxonomy instead of a scalar

A scalar return would be *worse* for this loop. `never_grasped` and
`wrong_object` earn the same reward — failure — and mean entirely different
things: one says the grasp stack is broken, the other says the safety property
was violated. The signal is shaped for a consumer that can act on the
distinction, which is exactly what an LLM is and what a gradient is not.

This reframes ablation A7. "Does the portable verifier's noise break
learning?" is really **does label noise break outer-loop search** — and the
noise model matters: the realistic verifier measures 0.00 false-success and
0.68 false-fail. A grader that almost never wrongly passes but often wrongly
fails is a very different problem from symmetric reward noise, and a
conservative grader mostly costs you *time*, not *safety*.

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

### 8.5 The idea loop, worked end to end

The "idea tree" is easy to mistake for a lab notebook. It is a gated artifact,
and the gate is what makes it work. This section follows one real idea —
**A6, `I16`** — from open to verdict.

#### Step 1: open the idea, stating the expectation first

```json
{"id": "I16", "ts": "...", "git_sha": "caf70e5e9",
 "idea": "teleport vs behavioral reset",
 "parent": null,
 "expect": "<pre-registered effect>",
 "status": "open"}
```

Two details carry the weight. The `git_sha` is stamped **at open time**, so
the claim is bound to the code that will be measured rather than to whatever
the tree looks like at write-up. And `expect` is filled in **before** any
episode runs — four expectations were registered for I16.

#### Step 2: the gate

`harness rollout` refuses to start if the branch has no open idea. You cannot
gather evidence first and decide what it meant afterwards. (`--no-idea-gate`
exists, is recorded in the run manifest, and is for machinery runs only — an
opt-out that leaves a trace is very different from an opt-out that does not.)

#### Step 3: run the matched arms

Paired 10-episode T1 arms, seeds 0..9, oracle verifier, idle machine, trusted
`--env-baseline origin/main`:

| arm | graph | pass@1 | wall | reset outcomes |
|---|---|---|---|---|
| teleport | `expert_t1.yaml` | 1.00 (10/10) | 6.4 min | 10 teleport |
| behavioral | `expert_t1_behavioral.yaml` | 0.80 (8/10) | 9.6 min | 7 success / 3 audited `fallback: true` |

#### Step 4: close it with a verdict

```json
{"id": "I16", "observed": "...", "verdict": "flat", "status": "closed"}
```

**`flat`** — 3 of 4 expectations met. Not `up`. The ablation did not confirm
what it set out to, and the record says so.

#### Why this is the example worth studying

The *miss* is the result.

The expected finding was the one from the literature: that the reset is itself
a manipulation task. That was confirmed — 30% of behavioral attempts could not
return the box and fell back to teleport. But the two failed **episodes** were
not caused by reset failure at all. They failed on **scene drift**: only the
delivered box returns, to a *sampled* slot, while every other box stays where
the previous episode left it. Episodes therefore run on progressively
non-canonical layouts, and 2/10 failed honestly on geometry that seeded curves
never see.

Teleport hides this entirely — same-seed teleport episodes always start from
the canonical layout.

Now consider what happens without pre-registration. You see 1.00 versus 0.80
and write: *"behavioral reset is harder, costs 20% success and +19 s per
episode."* Every word is true, and the finding is missed. The pre-registration
is what forced the gap between *expected mechanism* and *observed mechanism*
to become visible, and that gap is the publishable line:

> Curve numbers measured under teleport resets are an upper bound; a physical
> desk pays both the reset time **and the drift tax**.

The same pattern appears in T2's ideas, where two expectations were tracked
separately — `I14` closed **`down` on the success expectation with the safety
expectation met** — so a safety win could not paper over a performance miss.

#### The gap this section has to admit (open gap #266)

The raw `I16` entry is **not retained**. Idea trees are branch-scoped, live
under gitignored `runs/`, and the campaign directories are gone. What survives
on mainline is machinery smokes plus the findings files that quote the
research ideas.

So the sentence "3/4 pre-registered expectations met" is, right now, an
unverifiable claim in prose — exactly the thing the idea tree exists to
prevent. It is the same retention failure as the lost skills (§9.2), one
artifact over, and it undercuts a claim the project makes about auditability.
Issue #266 tracks it.

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

**H3 (skill reuse) — UNDECIDED on both suites, and the null is informative.**
The retail ladder (S1→S3) lost every library-arm cell to drift. The desk ladder
(T1→T4, the ASPIRE ablation as specified in §8.4.2) then ran to completion:
`met: null` under strict admissibility, 13 record-derived caveats. On T4 — the
only tier where both arms produced clean first-success numbers — the ratio is
**~1.03** (894 s vs 872 s): parity, not the ≤0.5 the hypothesis asks for.

The interpretable result is not "the library does not help". It is that **the
ladder's difficulty spacing prevented the question from being asked**: T1 and
T4 are solved by both arms inside one sub-budget, so there is no headroom for a
speedup to appear in; T2 and T3 are solved by neither, so there is no success
to accelerate. A transfer curve needs a tier that is hard but reachable, and
this ladder has none. That is a finding about instrument design, and it
generalizes past this project — an accumulation benchmark is only measurable in
the band between "trivial" and "impossible", and that band has to be located
empirically before the campaign, not assumed from the curriculum's shape.

Skill *reuse* was nevertheless verified live: arm L's T3 deliverable embeds
`s3-driver-v1` verbatim — a retail→desk cross-suite transfer, which is the
strongest form the H3 design hoped for. The mechanism works; the outcome delta
at these budgets was zero.

**A3 (params-only vs params+code) — the constrained arm won.** Denying an agent
the ability to author code, leaving only parameter edits, produced equal
held-out quality (1.0/1.0 both arms) at **half the tokens** (200k vs 396k), a
third of the wall clock, and one dev rollout instead of four. Where the registry
already covers the task, the schema is a subsidy: the agent does not have to
rediscover what a working system looks like. n=1 per arm on the easiest tier, so
this is directional — but it is the sharpest evidence the project has produced
for the substrate claim (H4), and it points somewhere unintuitive: the win came
from *removing* an affordance.

**A4 (Claude Code vs Codex) — both solve T1; the difference is style.** Identical
budgets, prompt, seeds and pin. Both reached 1.0/1.0 held out with zero
`wrong_object`. Codex reached first verified success sooner (8.1 vs 9.7 min)
and then kept iterating — five rollouts, 364k tokens, 73 minutes — while Claude
converged in two rollouts and stopped at 186k tokens and 36 minutes. At equal
quality one session cost roughly half the other. n=1 per arm at one budget on
the easiest tier: a lower bound, and reported as one.

**A5 (fleet scaling) — throughput saturates at ~4 lanes.** 1.6 → 4.1 → 4.3
successes per hour at N = 1, 4, 8 agents. Going from four agents to eight bought
+5% throughput for twice the agents and 2.2× the token burn; per-agent token
cost rose +22% then +31%, reproducing ENPIRE's super-linearity direction on a
single laptop-class host rather than a robot fleet. The result worth flagging is
what did *not* degrade: **holdout quality was contention-invariant at 1.0 on
every one of 13 lanes**, including a fleet-8 lane that never logged a dev-seed
success in-session yet scored 1.0 held out. Contention costs latency, not
correctness. The protocol deviation is recorded rather than buried: lanes shared
the host with their own simulator instead of one batched bridge.

**A6 (teleport vs behavioral reset) — teleporting hides a task and a cost.**
Paired 10-episode arms: teleport 1.00 pass@1 in 6.4 minutes; behavioral 0.80 in
9.6 minutes at +19 s per episode, with 7 successful resets and 3 audited
fallbacks. The reset is itself a manipulation task that fails sometimes — which
is precisely the parity with the real-world path that the fast inner loop
conceals, and the reason the ablation exists.

**H4 (hot swap) — measured at T0.** Phase-randomized, hot-swap median
iteration latency 32.4 s vs. relaunch 41.8 s (ratio 1.29, n=6 per path, zero
infrastructure failures); the mutation mechanism alone is ~1.7× faster.
Extremes overlap and **no significance or equivalence claim is made at n=6**.
The measurement is explicitly labelled unattested. Note also that the
registered H4 control is an equal-budget monolithic-script condition, which
has not yet been run — only hot-swap vs. relaunch has.

**H5 (safety) — holding, on a denominator that has grown by design.** Zero
`wrong_object` outcomes in 224/224 episodes across the three H2 campaign runs,
and zero across every campaign since: the desk H3 ladder, A3's two arms, A4's
two agent CLIs, A6's two reset arms, and all 13 A5 fleet lanes. Roughly forty
agent sessions have now authored motion code freely without producing a
wrong-medicine delivery, including eight running concurrently. Inadmissible H3
cells still do not contribute — a cell that cannot support a performance claim
cannot support a safety claim either — but the admissible campaigns above do.

The pattern across all of them is the point: thresholds declared in advance,
results reported with their denominators and their caveats, and at least one
verdict actively withdrawn by the project's own audit.

### 9.0 Phase 2 and Phase 3, closed

Both phases closed on 2026-08-16. Phase 2's eight DoD items are complete. Phase
3 closed at **five of six**, with the skill-library row recorded as **NOT MET at
3** against a target of ≥5.

That row is worth reading precisely, because a bare "3 of 5" invites the wrong
conclusion. Agents produced five evalcarded skills. Three are now in the
library. The other two are refused by ADR-37's registry floor on their own
evalcards — they measured 0.33 and 0.0, because they were authored against T2,
which no arm has solved. **Three was the ceiling, and the library reached it.**
The binding constraint is the unsolved tiers, not agent capability, and not the
governance path.

The third skill is the one worth dwelling on. `ik-transfer-v2` is a
`safety_class: motion` node — the governance-critical class, the one §9.4's
trust tiers exist for. An agent wrote it in response to a trace-cited
collision at a specific seed, shipped it with its own eval suite over a
population containing both the discovered failure and its unmodified
neighbours as a regression guard, and gave it a fallback to the stock
trajectory on IK failure so the change could never be worse than baseline. It
measured 1.0. It was then lost to the retention gap below, recovered,
provenance-verified against the campaign machine, reviewed, and human-merged
into the registry.

That is the full §9.4 path — author, evaluate, review, merge — exercised end to
end on the class that matters most. One instance is not a governance result,
but it is an existence proof that the path closes, which the phase could not
otherwise claim.

A separate constraint nearly hid all of this: three of the five skills were
authored inside campaign worktrees that no committed record pointed at, so the
first-pass §8.4 review could only cover two of five and this report initially
recorded the other three as lost. They were not lost; they were undiscoverable
from any machine but the one that ran the campaign. §9.2 treats that as its own
finding.

### 8.6 Running an autonomous campaign with integrity

A "campaign" is one research agent, one pinned worktree, one budget, one
scored deliverable. The machinery around it exists because the alternative —
"we ran an agent and it got better" — is an anecdote. Each control below was
added after a specific failure, which is the best way to learn them.

**Pinned worktree.** The agent works in a git worktree pinned to a recorded
commit, not in the live tree. Without this, the code changes underneath the
measurement and the result belongs to no particular version of the system.

**Replication arms must predate the analysis they replicate.** An early
replication arm read the first arm's committed findings *from inside its own
worktree* and "replicated" them. That run is kept, labelled contaminated, as
the cautionary tale. The rule now: a replication pins a commit that predates
any committed analysis of the same experiment.

> **This is the failure mode most likely to bite a hackathon team.** Your
> repository contains your results. An agent with repository access can read
> them. Independence is a property of what the agent could see, not of your
> intent.

**Session isolation.** Each session gets a fresh home directory; credentials
are seeded before and scrubbed after, in a `finally` block so a token cannot
outlive the session even on an unexpected exception. An occupied home is
rotated aside rather than reused, so an aborted attempt's state cannot leak
into the next launch.

**Budgets enforced from outside.** Token ceilings are counted from the live
API stream by the runner — not from a log the session could rewrite — using
new-token semantics (input + cache-creation + output, cache reads excluded).
Wall ceilings likewise. The agent cannot opt out of the ledger.

**Held-out scoring after the session ends.** Dev seeds are the agent's; the
held-out seeds are scored by the *runner*, after the agent's turn is over, with
the graph and library exactly as the agent left them. Dev numbers never
headline.

**Frozen-set audit after the fact.** Even though the rollout gate would have
refused a drifted environment, the campaign diffs the frozen paths against the
pin afterwards, so tampering is visible even for work that never triggered a
rollout.

**Everything recorded.** Stop reason, token and wall spend, rollout trajectory,
held-out result with failure taxonomy, frozen-set audit, skill-library state
before and after, and the runtime identity of the dora CLI — the last because
a version mismatch invalidated a scenario once, and neither `--version` nor a
committed hash could see it.

#### What the campaign machinery still cannot do

It cannot make a badly-designed ladder measurable (§9's H3 finding). It cannot
retain artifacts it was never told to keep — three skills and every research
idea tree were lost that way (§9.2, issue #266). And its controls are strong
against careless optimization and **untested against a capable agent
deliberately trying to pass**, which §12 states as an open threat rather than a
solved problem.

### 9.05 How to read the evidence (a skill worth acquiring)

Every number in §9 comes from a committed findings file, and those files are
written to be quotable *including their own caveats*. Learning to read them is
the transferable skill this project teaches, so here is the method.

**Start with the denominator, not the number.** "0 wrong-object" means nothing
until you know it is 0 in 224 episodes across three campaign runs, and that
inadmissible cells were excluded from that count rather than quietly included.
A rate without its denominator is a slogan.

**Find the unit.** Is this per-episode, per-rollout, or per-attempt (§7.5)? A
"1.0 pass@1" from one session and a "1.0 pass@1" across twenty attempts are
different claims wearing the same notation.

**Look for the flags.** Campaign cells carry machine-derived flags —
`wipe_leak`, `frozen_drift`, `treatment_drift`, `holdout_partial`. The rule is
that flagged cells stay in the table as history but never enter a verdict. When
a findings file reports `met: null` with thirteen caveats, that is the system
working, not the analysis being evasive.

**Check whether it is attested.** ADR-24 introduced an environment fingerprint.
Measurements taken before it, or from a live tree rather than a pinned
worktree, are labelled **UNATTESTED** and make no reproducibility claim. H4 is
the standing example: a real measurement, honestly labelled, that you may not
cite as reproducible.

**Ask what the number is an estimate *of*.** This catches the most common
mistake. A pass@1 from many episodes of one graph estimates *that graph*. It
does not estimate the agent that produced the graph, and it certainly does not
estimate agents in general.

**Read the "what this does NOT establish" section.** Most findings files have
one. `analysis/a1/a1_table.md` has an explicit one; the VER-6 fidelity README
lists four separate limits including the one that turned out to matter most —
that it was measured at rung L0.

#### A worked example of a number that changed meaning

The verifier-fidelity headline was first reported as agreement **0.29** over 31
episodes, with 0.00 false-success and 0.88 false-fail. After the VER-13 fusion
amendment it was recomputed over *the same recorded episodes* as **0.45** /
0.00 / 0.68.

Both numbers are in the repository. The first is preserved as the
pre-amendment measurement rather than overwritten, because the honest record is
that a change to the fusion rule moved the metric — and a reader needs to know
that the scorer itself has a version. The rule the project applies to itself
here is worth memorizing:

> Preserve committed findings as dated evidence. When a verdict changes, update
> the overview that points at the finding — **never edit the finding to match
> the new story.**

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

**H5's zero is a denominator, not an absolute.** Zero wrong-object outcomes is
meaningful and bounded. It is not "the system cannot deliver the wrong
medicine"; it is "in these recorded episodes under these conditions, it did
not". The safety argument in §11 rests on the guard's structure, and the
measurement is corroboration rather than proof.

### 9.2 What the governance review found: three harness defects, zero agent faults

Phase 3's final DoD item is a human review of every agent-authored skill, with
written notes, as governance-paper data. Running it produced a result the
project did not anticipate and is reporting rather than smoothing.

**Three flags were raised against agent code. All three turned out to be
findings about the harness.**

1. **The skill eval floor was self-graded.** `harness skill register` refuses a
   measured pass rate below `min_pass_rate` — but that threshold was read from
   the candidate's own `eval.yaml`, and only checked for being a float. A skill
   shipping `min_pass_rate: 0.0` registered at 0.0 and the gate reported
   success. One campaign skill did exactly that. The agent's reasoning was
   documented and coherent — it was using registration to *attest* a node's
   source so its graph would validate, not to claim quality — which makes the
   defect a conflation in the gate rather than gaming by the agent.
   Fixed: an absolute floor beneath every self-declared one (ADR-37).

2. **Campaign deliverables were never retained.** Worktrees live under a
   gitignored directory with no archival step, so a campaign record could name
   a registered skill that no reviewer could locate. Three of five skills were
   in this state, and this report initially recorded them as gone. They existed
   the whole time on the machine that ran the campaigns. Fixed forward: each
   session now archives its working tree to a ref in the shared object store.

3. **A validator rule proposed in response to flag 1's sibling would have
   rejected the frozen corpus.** The rule — policy nodes must not import the
   realistic verifier's detector — was approved before being calibrated. Two
   curated-core nodes and the frozen reset service import it; enforcing the rule
   would have rejected two frozen expert graphs. Worse, the skill it was
   written against never had the alleged import at all. The rule was dropped.

The residue of flag 3 is a real measurement issue rather than a routing one: at
perception rung L2 the policy and the realistic judge would share a detector
backbone, so their errors correlate and agreement overstates independence. No
reported fidelity number is affected — all were measured at rung L0, where the
policy calls no detector — but the trap springs the wrong way, since an L2 run
would produce a *better* agreement and read as improvement. Every fidelity
report now states whether it is an independence claim.

**Why report this at all.** A governance review that finds nothing against the
subjects it reviews is usually written up as a clean bill of health. Here the
same exercise found three defects in the reviewing machinery, and the honest
summary is that the fence had gaps the agents never exploited — one because an
agent documented its reasoning instead of hiding it, one because nobody
attacked an unretained artifact, one because a rule was caught before it
shipped. The first-pass review could only examine two of five skills, and that
number belongs in any claim this project makes about human-in-the-loop
governance of agent-authored robot code.

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

### 10.11 Where SmolVLA actually sits: base training, SFT, or RL?

The first learned policy landed as a node in August 2026, and the honest
answer to "which ML stage is this?" is **none of them**.

| stage | done by whom | where |
|---|---|---|
| **base training** | LeRobot upstream — `smolvla_base`, ~450M params, on SO-100/SO-101 tabletop data | not in this repository |
| **supervised fine-tuning** | not done | named as the follow-on |
| **reinforcement learning** | not done | structurally awkward here; see below |

What the repository does is **zero-shot evaluation of a frozen checkpoint**.
`vla_backend.py` loads the policy, calls `.eval()`, and runs inference under
`torch.no_grad()`. No weights change. Model identity is pinned by Hugging Face
revision hash in the manifest — the environment-hash discipline extended to
weights, so swapping a checkpoint moves the attested identity exactly as a
scene edit would.

#### What the bring-up deliberately does not claim

The ADR governing it is unusually direct, and students should read it as a
model of how to register an expectation you expect to lose:

> Zero-shot on the Genesis pharmacy scene — even the SO-101 profile — is
> **EXPECTED to score at or near 0**: the bring-up deliverable is the typed
> integration (node, manifest, eval graph, preemption rule, dependency extra)
> plus the honest zero-shot baseline as the first data point, **NOT a working
> policy.**

The interesting wrinkle is that `smolvla_base` was trained on SO-100/**SO-101**
data and AISLE ships an SO-101 embodiment profile. This is therefore close to
the *best* case for zero-shot transfer, and it is still expected to fail — which
makes it a meaningful floor rather than a strawman. Registering a 0.0 as a real
data point rather than waiting for a flattering number is the same discipline as
closing an idea `flat`.

#### The one new safety rule the integration required

A VLA emits **action chunks** — a bounded sequence of joint targets from one
inference — which classical nodes do not. That creates a failure mode the
architecture had not needed a rule for: two inferences, or two policies,
fighting over the arm. ADR-38 fixes it before the first motion inference runs:

1. **One in-flight chunk.** A new result *replaces* the queued remainder at the
   next action boundary; chunks never interleave.
2. **The guard is unchanged.** Every chunk element still traverses budget-guard
   clamping. Preemption is a queueing rule, not a safety mechanism — an
   important distinction, because it means the safety argument does not depend
   on the preemption logic being correct.
3. **Reset flushes** any queued chunk.
4. **Staleness floor.** A chunk computed against observations older than
   `VLA_STALE_NS` is dropped rather than executed — a slow inference must never
   act on a world that has moved on.

#### The obstacle to fine-tuning that nobody expects (open gap #267)

The demonstration data already exists: expert graphs run at 0.98–1.0 pass@1 and
every episode is recorded as Arrow traces. So SFT looks like a small step. It
is not, because of one question with no obviously right answer:

**Which signal is the demonstration label?**

Every motion command traverses the budget guard, which does not merely veto —
it **clamps**. Both signals appear in the trace:

- `joint_cmd` — what the policy *proposed*
- `joint_cmd_safe` — what the arm *executed*

They differ exactly when the proposal was out of bounds, which is exactly the
interesting part of the distribution.

*Train on proposals* and the model learns the expert's intent including
corrections it never sees, so it will reproduce out-of-bounds behaviour and
depend on a guard being downstream — fine in this harness, dangerous as a
portability claim, and it means the policy's safety record is really the
guard's.

*Train on executed actions* and every label is legal, but the labels are a
mixture of two processes — the policy's output and the guard's correction — so
the model imitates an intervention whose trigger it cannot observe. That is a
hidden-confounder setup, and the classic off-policy correction problem
introduced here by a *safety mechanism* rather than by exploration.

There is a genuine tension in the architecture here, and it is worth naming
plainly for students: **structural safety and clean credit assignment pull
against each other.** The guard is what makes free agent authorship safe (H5);
it is also what muddies the supervision signal. The good news is that the trace
records both signals, so the divergence rate is measurable — and that statistic
is itself a finding, because it quantifies how much of the expert graph's
competence is actually the guard's.

#### Why RL is a poor fit for this scorer

Beyond the absence of a reward (§7.6), three properties make RL unattractive
here without changes:

1. **Sparse terminal binary signal** over episodes of tens of seconds.
2. **Non-reproducible physics** — ADR-26 makes full-episode outcomes
   statistical under Metal, so the same policy and seed need not produce the
   same return.
3. **The clamping problem above**, in its more severe form: the executed action
   differs from the proposed action, and nothing models the correction.

None of this says RL is impossible here. It says the environment as built is
shaped for a reader, and turning it into something shaped for an optimizer is a
design project rather than a configuration change.

#### The determinism layer nobody has written (open gap #268)

The determinism contract (§6) layers nondeterminism sources and states what
each promises. A learned policy adds one no layer names: sampling, batching,
and non-deterministic kernels. The report has posed this as an open question —
*what should layer (e) say?* — and as of the bring-up it is live rather than
hypothetical.

The subtle one is not kernels but timing. ADR-38's staleness floor is expressed
in **sim** time while inference latency is **wall** time, so on a loaded host a
different set of chunks is dropped and the executed trajectory changes **with
every recorded seed identical**. That is the wall-versus-sim coupling class
that has already bitten this project once in a graph test, reappearing in the
policy path — and it means a zero-shot baseline could fail to replay before it
has even been used as a baseline.

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

**Operation as a research question (now H6).** Monitoring, diagnosing, and
repairing a *running* system is registered as a claim rather than left as an
aspiration. The open part is methodological: what is a fair fault-injection
protocol, and how do you score a recovery that is correct but slower than a
human's?

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
and wall-timer control loops removed.

**Phases 2 and 3 closed (2026-08-16).** H1, H2, H4 and H5 measured; H3 run on
both the retail and desk ladders and reported UNDECIDED with the difficulty
spacing as the finding; ablations A1, A3, A4, A5 and A6 measured and committed;
the T1 and T2 tier curves established; the agent-PR governance review completed
and signed. Phase 3 closed at five of six DoD rows, the skill library falling
short at 3 of a required 5 — see §9.0 for why 3 was the ceiling, and why the
library reaching it is a better result than the number suggests.

**In flight.**

| Work | State |
|---|---|
| Deterministic lockstep turns (ADR-30) | **Implemented** across every measured graph; turn plans are committed runtime inputs, frozen with the graphs they compile from, and the validator refuses a stale plan at the gate |
| T4 dialogue tier | **Implemented** (ADR-32); solved by both H3 arms inside one sub-budget |
| H3 | **Re-run on the desk ladder and reported.** Not blocked any more — UNDECIDED under strict admissibility, with the instrument, not the hygiene, as the limiting factor this time |
| Sandbox trust tier | see below — the one governance thread the `ik-transfer-v2` registration did *not* close |
| Sandbox trust tier | Newly identified gap: ADR-37's floor leaves no legitimate way for an agent to declare a node that merely needs an id to validate. §9.4's `sandbox → reviewed → certified` roadmap names it; it does not exist yet |
| Retail suite hardening | Ongoing |

**The standing scientific challenge.** T2 and T3 remain unsolved by any arm at
session budgets — desk-H3 both arms, A3, A4. Everything above is infrastructure
around a curriculum whose middle is still open, and closing it is the
prerequisite for a meaningful accumulation result (§9.1).

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

---

## Appendix C: exercises and hackathon tracks

These are graded by how much of the system you must understand, not by lines
of code. Each names the sections to read first and what "done" looks like.
Every one is a real gap or a real mechanism — none is a toy.

### Warm-up (30–90 minutes each)

**C1. Break the validator on purpose.** Read §4. Take `graphs/expert_t0.yaml`,
copy it, and introduce one fault: a schema mismatch, a missing producer, an
`oracle_state` edge into a policy node, a motion path that bypasses the guard.
Run `harness validate` on each. *Done when* you can predict the error code
before running, and you can explain which alternative explanation each check
kills. Add your best one to the bad-graph corpus (VAL-7 requires every
agent-discovered failure class to enter it).

**C2. Read one episode end to end.** Run the T0 expert graph for two episodes
and follow one from `episode_goal` to `episode_result` using
`harness traces query`. *Done when* you can state, from the trace alone, why
that episode passed or failed and which node you would suspect first.

**C3. Find the guard clamping.** Using the traces from C2, compare `joint_cmd`
with `joint_cmd_safe`. *Done when* you can say how often they differ and by how
much. This is a real open question (§10.11, issue #267) — the divergence rate
on the expert corpus is not currently recorded anywhere, and it quantifies how
much of the expert graph's competence belongs to the guard rather than the
policy.

### Intermediate (a day)

**C4. Write a skill and register it.** Read §5.5. Author a node providing an
existing capability, ship it with a `skill.yaml` and an `eval.yaml`, and run
`harness skill register`. *Done when* the registry holds an evalcard your code
did not write. Try to register a failing skill and read the refusal; try to set
`min_pass_rate: 0.0` and read that one too (ADR-37).

**C5. Reproduce a measurement, then break its reproducibility.** Pick a
committed finding in `analysis/`. Re-run it. *Done when* you either reproduce
the number or can say precisely why not — and can name which attestation gate
would have caught the difference.

**C6. Design an idea properly.** Read §8.5. Open an idea with a
pre-registered expectation, run the matched arms, and close it honestly. *Done
when* you have closed one `down` or `flat`. An idea tree of all `up` verdicts
is the signature of a contaminated arm, not a good researcher.

### Hard, and genuinely open (a weekend or more)

**C7. Close the applicability gap (#264).** Design the schema extension that
tells a later agent *when* to reach for a skill, not just that it worked. The
hard part is not the field — it is making it something the validator can check,
because a field nothing enforces is documentation. Bring an ADR.

**C8. Build the sandbox trust tier (#265).** ADR-37's floor closed a real hole
and removed the only way an agent could declare a node that merely needs an id
to validate. §9.4 names `sandbox → reviewed → certified`; only the middle
exists. *Done when* an unproven node can be admitted for validation while being
structurally unable to count toward the DoD, be reused by another agent, or
hold `safety_class: motion`.

**C9. Solve T2.** The label-reading tier sits at **0.08** expert pass@1 and no
agent arm has solved it within a session budget. The reads themselves are
accurate when the arm is parked — the failure budget is dominated by tour
mechanics: reachability of read poses, transit safety, refusal cascades. This
is the single largest open scientific item in the project, and it is what caps
the skill library and prevented H3's accumulation question from being asked
(§9). It is also a completely fair fight: the expert graph is committed and its
failure taxonomy is published.

**C10. Make the fidelity measurement mean something at L2 (#248 lineage).**
Verifier fidelity has only ever been measured at rung L0, where the policy uses
ground-truth poses. At L2 the policy and the judge would share a detector
backbone, so agreement overstates independence. Design and run the comparison —
shared backbone versus an independent judge — and report both. The gap between
those two numbers *is* the correlation bias, currently unquantified.

**C11. Decide the SFT label question (#267), then fine-tune.** Read §10.11.
Settle `joint_cmd` versus `joint_cmd_safe` with an ADR *before* training, then
fine-tune SmolVLA on expert-graph demonstrations and measure against the
zero-shot baseline on identical seeds and scorer. This is the largest piece of
genuine ML work currently available in the project.

### For the reviewer-minded

**C12. Try to fool the evidence system.** The threat model in §12 is explicit
that the controls are strong against careless optimization and **untested
against deliberate circumvention**. Attempt a run that looks admissible and is
not. Every successful attack is a contribution; the project has already had
three found by ordinary review (§9.2).

---

## Appendix D: open gaps, with issue numbers

A contributor-facing register of what is known-missing. These are not bugs —
they are places where the design is incomplete and the incompleteness is
understood.

| # | Gap | Why it matters | Section |
|---|---|---|---|
| [#264](https://github.com/heyong4725/aisle/issues/264) | Skill manifests record evidence but not **applicability** | As the library grows, undifferentiated same-capability entries make selection harder — the accumulation benefit can invert | §5.5 |
| [#265](https://github.com/heyong4725/aisle/issues/265) | No **sandbox trust tier** | ADR-37's floor leaves an agent no legitimate way to declare an unproven node so its graph validates | §5.5 |
| [#266](https://github.com/heyong4725/aisle/issues/266) | **Idea trees are not retained** | The pre-registrations that make findings falsifiable are gone; "3/4 expectations met" is currently unverifiable prose | §8.5 |
| [#267](https://github.com/heyong4725/aisle/issues/267) | **SFT label undecided**: `joint_cmd` vs `joint_cmd_safe` | Structural safety and clean credit assignment pull against each other; must be settled before any fine-tune | §10.11 |
| [#268](https://github.com/heyong4725/aisle/issues/268) | **No determinism layer for inference** | The staleness floor couples sim time to wall time, so a loaded host can change the trajectory with every seed identical | §10.11 |
| [#269](https://github.com/heyong4725/aisle/issues/269) | **"Safe Learning" is undefined** in the project's own naming gloss | Students reasonably expect a training loop and find none | §3.5 |

Two further threads are open and owner-facing rather than contributor-facing:
the eval's **seed set and episode count remain candidate-chosen** (the same
self-grading shape ADR-37 closed, one field over), and **T2/T3 remain unsolved**
at session budgets, which is the scientific prerequisite for a meaningful
accumulation result.

---

## Appendix E: common misconceptions

Fast answers to the questions that come up every time someone new reads this
report. Each links to where the long version lives.

**"AISLE trains robot policies."** No. There is no training code in the
repository — no `.backward()`, no optimizer, no `def train(`. The one learned
policy present runs inference under `torch.no_grad()` with weights pinned by
revision hash. See §3.5 and §10.11.

**"It's an RL environment, so there's a reward function."** The verifier emits
a status enum plus a failure class, never a scalar. The asymmetric penalty
("wrong medicine is 10× worse") is English in the goal text, not a coefficient.
The closest classical framing is held-out model selection, not RL. See §7.6.

**"More episodes would make the results stronger."** For a fixed graph, yes.
For claims about *agents*, no — episodes and attempts answer different
questions, and this is the most common analytical error made about the results.
See §7.5.

**"The agent's memory is what accumulates."** No. Sessions start fresh.
Anything that survives had to become an artifact — a registered skill, a graph
diff, an idea-tree entry. That is what makes the wiped-versus-library
comparison meaningful. See §3.5.

**"H3 failed, so skill reuse doesn't work."** H3 is UNDECIDED, which is not
"not met". Reuse demonstrably occurred — a retail-registered skill appears
verbatim in a desk deliverable. What the campaign could not do is *measure a
speedup*, because no tier sat between trivial and impossible. The finding is
about the instrument. See §9.

**"The skill library only reached 3 of 5 because the agents underperformed."**
Three was the ceiling. Two further skills exist and are refused by the registry
floor because they measure 0.33 and 0.0 — they were authored against T2, which
no arm has solved. The binding constraint is the unsolved tier. See §9.0.

**"The safety record proves the system is safe."** It proves that in the
recorded episodes under these conditions, no wrong medicine was delivered. The
safety *argument* is structural (§11); the measurement corroborates it. A zero
is always a denominator claim.

**"The guard means learned policies are safe to deploy."** The guard clamps
proposals, so a policy's measured safety record is partly the guard's. That is
precisely the ambiguity issue #267 has to resolve before fine-tuning, and it is
a genuine tension rather than an oversight. See §10.11.

**"The frozen set is just the `env/` directory."** It has widened three times,
each time on the same argument: the unit of the fence is *what a result depends
on*, not the directory it lives in. Read the constants in `tools/env_hash.py`,
never a list in prose — a second copy of the fence is exactly how one component
stayed outside it. See §4 and `docs/architecture.md`.

**"These docs are the source of truth for status."** Only the README status
table is. Everything else stamps a date and defers to it on conflict — because
five orientation pages once answered the same question differently, which is
the failure that produced the rule.

---

## Appendix F: recurring design patterns

The same handful of moves appear throughout this system. They are worth
extracting, because they transfer to any project where an autonomous process
produces artifacts someone later has to trust.

### F1. Fail closed, and make the refusal informative

Whenever the system cannot establish something, it refuses rather than
assuming. The environment hash cannot be verified → the rollout does not
start. The perception rung cannot be resolved → assume the strictest rung and
report. Fidelity cannot establish which rung a run used → report
`independent: null`, never `true`.

The pairing matters: fail-closed alone produces a system that says "no" a lot.
Fail-closed *with a machine-actionable message naming the fix* produces a
system an agent can learn from — which is the entire H1 lesson.

### F2. The unit of the fence is what a result depends on

Not the directory it lives in. This argument widened the frozen set three
separate times — mobility verdicts, ADR-30 turn plans, skill-gate eval graphs
— and each widening followed a near-miss where something load-bearing changed
without moving a hash.

Corollary: **never hand-maintain a second copy of the fence.** A prose list of
frozen paths goes stale silently, and that is precisely how one component
stayed outside it while everyone believed otherwise.

### F3. An opt-out that leaves a trace is not an opt-out

`--no-idea-gate` skips the idea requirement and is recorded in the run
manifest. `--env-baseline local` permits an unattested run and stamps the
result as making no reproducibility claim. `allow-unproven` exists and is never
set for agents.

The pattern: do not remove escape hatches, because people need them. Make the
hatch *self-reporting*, so a result produced through it cannot later be quoted
as though it were not.

### F4. Specify the rule before the thing it governs runs

ADR-38 fixed the VLA chunk-preemption rule *before* the first motion inference.
ADR-37's floor was written before the next campaign, not after the skill that
exposed the hole. The verifier's toppled-but-correct question is decided in the
spec rather than improvised when it first occurs.

The failure this prevents is subtle: a rule written after seeing the data is
indistinguishable — to a reader, and often to its author — from a rule chosen
because of the data.

### F5. The candidate does not grade the exam

Two variants of the same defect appeared one layer apart: the skill's eval
*graph* was editable by the candidate (fixed by freezing it), and the eval's
*threshold* was declared by the candidate (fixed by an absolute floor). The
general form:

> Any artifact that certifies a learner must be outside the learner's reach —
> including the artifact that defines what "passing" means.

The still-open instance of this pattern is the eval's seed set and episode
count, which remain candidate-chosen.

### F6. Test that the rule is used, not just that it works

A recurring near-miss: a rule is implemented and unit-tested, and nothing tests
that the system *invokes* it. A tamper audit re-listed frozen paths by hand, so
when the frozen set widened the audit did not follow — the rule was correct and
uninvoked. When you add a check, add a test that removing the *call* fails,
not only one that the check computes correctly.

### F7. Preserve the wrong answer

Superseded measurements stay in the repository with their dates: the
pre-amendment fidelity number, the contaminated campaign arm, the invalid
attested cell. The rule is to update the *overview* that points at a finding
and never edit the finding to match a newer story.

This costs almost nothing and buys the ability to answer "when did we know
that?", which is the question that separates a research record from a
marketing page.

### F8. State the control you registered, not the one you ran

H4's registered comparison was against an equal-budget monolithic-script
condition. What was measured was hot-swap versus relaunch. Both are
interesting; they are not the same claim, and the report says so rather than
letting the measured number stand in for the registered one.

### F9. A null result is about the instrument as often as the subject

H3 measured no accumulation speedup. The reason was not that libraries do not
help — it was that no tier sat in the band between "both arms solve it
trivially" and "neither arm solves it at all". An accumulation benchmark is
only measurable in that band, and the band has to be located empirically
*before* the campaign.

When an experiment returns null, the first question is whether the instrument
could have detected the effect had it been there.

---

## Appendix G: the failure taxonomy, and how to diagnose each class

`episode_result.failure` comes from a closed vocabulary. Class attribution
belongs to the oracle verifier; the realistic verifier's failure field is
informative and never compared class-wise. This table is the diagnostic
starting point when you read a run.

| Class | What it means | First thing to check |
|---|---|---|
| `wrong_object` | A non-target item entered the tray region | **Stop and investigate.** This is the safety-critical class the whole asymmetric-penalty design exists around; it has never occurred in a recorded episode |
| `never_grasped` | The episode ended with the target never acquired | Perception first — did the pose/identity estimate resolve? Then reachability: was a valid grasp pose ever produced? |
| `dropped` | Grasped, then lost before delivery | Gripper command timing and contact parameters; thin-box grasping is contact-parameter sensitive by construction |
| `collision` | Contact that ended the episode | Transit geometry. This dominates T2's failure budget and is what motivated the routed-transfer skill |
| `timeout` | The episode budget expired with no terminal verdict | Distinguish "slow but working" from "stuck": check whether the arm was still issuing commands, and whether the run was wall-clamped |
| `misplaced`, `misaligned`, `overhang` | Retail placement-quality failures | The planogram placement criteria — position, yaw, front-face, overhang, neighbour alignment — each reported separately so you know which one failed |

Two reading notes that save time:

**A refusal is an honest failure.** Several nodes refuse rather than guessing —
a pose estimator that cannot establish identity declines to publish. A refusal
cascade shows up as `never_grasped` or `timeout`, not as a wrong action, and
that is by design: the asymmetric penalty makes not-acting strictly better than
acting wrongly.

**The fallback flag matters.** A behavioral reset that exhausts its attempts
falls back to teleport and records `fallback: true`. An ablation that ignores
that flag is measuring a mixture of two reset regimes and will report a number
that belongs to neither.
