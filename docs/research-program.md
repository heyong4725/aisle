# AISLE research program: agents, evidence, and model orchestration

<!-- status-snapshot:2026-08-28 canonical:../README.md#status -->
**Technical framing, August 2026.** Status snapshot: 2026-08-28, commit `93de5e0`; design-doc Phases 2 and 3 closed, the model-tier agenda in §here EXECUTED as Phase 5 (closed 2026-08-27 at measured end-states — VLA competence/latency separated by the lockstep-eval condition, five VLM-judge configurations refused, the env-ladder swap proven mechanically; ledger in [next-phases.md](next-phases.md)), and the hardware entry prepared (ADR-phase6-prep). Any verdict restated here is a dated summary for context; the [README status table](../README.md#status) is canonical and wins on conflict (issue #142). This report states the research object,
experimental logic, evidence standard, and model-integration agenda for AISLE.
It is descriptive rather than normative: the numbered specifications define
what the implementation must do, the [README](../README.md#status) owns current
status, and committed findings under [`analysis/`](../analysis/) own measured
results.

## Abstract

AISLE studies autonomous engineering of embodied systems. A coding agent is
given a task, a typed capability registry, a constrained robotics runtime, and
tools for validation, rollout, diagnosis, and skill registration. It must
compose a robot system, inspect failures, improve the system under a budget,
and retain useful work for later tasks. The robot system is expressed as a
dora dataflow; Genesis, trusted verification, reset, motion guarding, and an
evidence harness close the experiment loop.

The central research question is:

> Can AI coding agents autonomously build, diagnose, improve, reuse, and safely
> operate robotic systems when those systems are composed as typed dora
> dataflows?

Here, **autonomously** means within a human-governed research envelope. Agents
may change the designated graph, policy, parameters, nodes, and candidate
skills; they may not redefine the task, silently change the scorer, bypass the
motion guard, inspect held-out answers, or promote safety-critical artifacts
without the repository's review gates. **Safely operate** means that AISLE can
test and enforce stated structural and task-safety properties. It does not mean
that a simulator result constitutes hardware certification.

The manipulation and retail tasks are instruments for answering this question,
not the final object of study. A successful pick matters because it is an
observable consequence of a complete engineering loop. Without evidence that
identifies the agent, code, graph, environment, seeds, budget, runtime, and
verdict, however, the pick cannot tell us which part of the loop worked or
whether the result can be repeated.

## 1. The research object

AISLE contains two nested loops that must not be conflated.

```text
OUTER RESEARCH LOOP (between episodes)
  agent proposes an idea
    -> edits graph, parameters, node code, or a skill
    -> validates the typed dataflow
    -> runs budgeted episodes
    -> reads traces and failure classifications
    -> accepts, revises, or reverts the idea
    -> optionally registers an evaluated skill

INNER ROBOT LOOP (within an episode)
  observe -> perceive/reason -> propose action -> guard -> actuate
          -> observe effects -> verify -> record
```

The coding agent operates in the outer loop. A classical controller, VLA,
world-model planner, WAM, or hybrid graph may operate in the inner loop. This
separation lets AISLE ask both whether the agent improves a system and which
kind of runtime system it chose to build.

The unit under study is therefore not a foundation model in isolation. It is
the following bounded system:

```text
research system = agent + contract + tools + registry + mutable artifacts
                  + dora runtime + trusted envelope + campaign budget
```

Changing any of those terms can change the result. A model comparison that
does not pin the rest of the system is not an AISLE causal comparison; it is a
new system observation.

### 1.1 Why typed dataflows are the experimental substrate

The dora graph gives the agent a structured action space. Nodes advertise
capabilities and typed ports; edges make dependencies visible; graph validation
can reject schema, rate, privilege, and safety violations before a rollout;
and a successful subgraph can be named, evaluated, and reused. The graph also
creates a common boundary around classical algorithms and learned models.

This produces five properties that a monolithic script does not provide by
default:

1. **Composability:** a capability can be selected and connected through an
   explicit contract.
2. **Diagnosability:** traces and failures can be attributed to a node, topic,
   or boundary.
3. **Replaceability:** an implementation can be swapped while its topic
   contract remains stable.
4. **Governability:** privileged state and motion authority can be constrained
   structurally.
5. **Reusability:** a working node or subgraph can become an evalcarded skill
   rather than an untracked code fragment.

These are hypotheses about the substrate, not assumptions that the current
implementation has already proven.

## 2. Research questions and falsifiable claims

The existing H1-H5 hypotheses divide the central question into observable
parts. Each should be allowed to fail or remain undecided.

| Question | Existing hypothesis | What is varied | What is observed | What would weaken the claim |
|---|---|---|---|---|
| Can an agent build a runnable system? | H1, composition | agent, registry and task prompt under a fixed environment | schema validity, launch rate, validate-fix cycles, first nonzero success | valid graphs do not launch, or require substantial hidden human repair |
| Can it diagnose and improve the system? | H2, iteration | access to traces, failure taxonomy, and editable artifacts | held-out pass rate, time/tokens/rollouts to threshold, idea outcomes | dev gains disappear held-out, or improvement exceeds the budget |
| Does prior work compound? | H3, accumulation | persistent evaluated skill library versus wiped memory | later-task time-to-success, reuse count, retained performance | no matched admissible arm exists, or reuse costs more than rediscovery |
| Does the substrate improve engineering? | H4, substrate | hot-swap/dataflow workflow versus the registered equal-budget monolithic-script control (only hot-swap vs relaunch measured so far) | iteration latency, failures, change locality, auditability | speed comes from unequal setup or evidence is not attributable |
| Does freedom remain bounded? | H5, safety | agent-authored policies and motion code under the same guard | wrong-object rate, guard interventions, rejected bypasses, unsafe proposals | motion bypasses the guard or harmful outcomes are hidden by the metric |

H1-H5 are system hypotheses. They do not by themselves establish that one
robot policy architecture is best. VLA, world-model, and WAM studies add a
second family of controlled comparisons inside the same harness (Section 6).

### 2.1 Measurement axes

AISLE should report results on more than task success:

| Axis | Example measures | Why it matters |
|---|---|---|
| Capability | pass@1, pass@k, failure taxonomy, task-tier coverage | whether the resulting robot system works |
| Engineering efficiency | wall time, tokens, rollouts, validate-fix cycles, time to threshold | what autonomy costs |
| Safety | wrong-object outcomes, guard clamps/rejections, collision and timeout outcomes | whether improvement stays inside the trust envelope |
| Reuse | skill retrievals, successful reuses, transfer speed, regression rate | whether experience compounds |
| Portability | embodiment, perception-rung, environment, and verifier transfer | whether an artifact survives boundary changes |
| Auditability | attributable graph diffs, complete manifests, trace coverage, admissibility flags | whether a claim can be independently inspected |

No single scalar combines these without hiding a tradeoff. A faster policy that
uses more inference compute, a more conservative verifier that increases false
failure, and a successful agent that consumes twice the research budget should
be shown as different points on a frontier rather than forced into one score.

## 3. Why experiments are necessary

A robotics demo answers “did this run succeed?” An experiment asks “what
caused the change, under what conditions, how often, and with what failure and
safety costs?” AISLE needs experiments for four reasons.

### 3.1 Attribution

An agent can change code, graph topology, parameters, installed dependencies,
and strategy in one session. The simulator and learned models may also be
stochastic. A before/after video cannot distinguish a better idea from easier
seeds, environment drift, extra compute, a changed verifier, or contamination
from another arm's findings. Frozen treatments, matched arms, and provenance
turn an observed difference into an attributable one.

### 3.2 Generalization

Agents see development seeds and can overfit them just as a learned policy can.
Held-out evaluation asks whether the resulting artifact generalizes beyond the
episodes that guided it. Independent agent sessions are also necessary: many
episodes estimate one artifact's task success, but they do not estimate how
reliably a fresh research agent will discover that artifact.

### 3.3 Safety and trust

Success alone is an unsafe objective. A system could increase pass rate by
moving faster, exploiting simulator artifacts, touching distractors, bypassing
a guard, or using privileged state. AISLE therefore measures prohibited and
adverse outcomes and records guard behavior alongside success. Structural
checks reduce the reachable unsafe space; experimental evidence tests whether
the checks hold during free iteration.

### 3.4 Scientific memory

The durable output of an autonomous research system is not only its best graph.
It is a record of attempted ideas, negative results, boundary conditions, and
reusable components. Clean negative or inconclusive results prevent later
agents from repeating invalid work. An evalcard carries measured scope with a
skill so that “reuse” means applying an artifact with known evidence, not
copying code whose operating conditions were forgotten.

## 4. Why evidence collection is part of the system

Evidence is not paperwork added after a rollout. It is the mechanism that
connects an agent action to a defensible claim.

```text
question
  -> preregister idea and expected effect
  -> freeze treatment, controls, budget, and evaluation protocol
  -> validate the graph and trust boundaries
  -> execute seeded episodes
  -> record traces, outcomes, costs, and interventions
  -> audit code/environment/treatment integrity
  -> analyze admissible records
  -> state a scoped claim with limitations
```

If a link is missing, the appropriate result is `UNATTESTED`, `INADMISSIBLE`,
or `UNDECIDED`—not a reconstructed success story.

### 4.1 Minimum evidence chain

| Layer | Record | Question it answers |
|---|---|---|
| Research intent | agent/model identity, contract, prompt or task, idea parent, hypothesis, expected effect | what was the agent trying to change? |
| Treatment | git SHA, diff, graph hash/snapshot, manifests, skill/evalcard state | what artifact was actually evaluated? |
| Environment | frozen-set hash, lock-derived environment fingerprint, platform/backend/device, dora revision, simulator configuration | in what world and software stack did it run? |
| Protocol | tier, perception rung, verifier, reset mode, seeds, held-out boundary, token/wall/rollout budgets | which experiment was executed? |
| Runtime | launched-node inventory, topic/Arrow traces, timestamps, video, model inference metadata | what happened inside the system? |
| Outcome | per-episode result, failure class, safety events, verifier sidecars, duration and resource spend | what did the experiment observe? |
| Audit | post-run trusted inventory, contamination/drift flags, exclusions, reviewer notes | is the record admissible for the proposed claim? |

For learned-model nodes, the treatment record must also identify at least the
checkpoint revision or digest, preprocessing/tokenization code, precision and
quantization, inference backend, sampling or decoding parameters, observation
window, action representation, device placement, and model-specific random
seed where available. A model family name is not enough to reproduce a
treatment.

### 4.2 Reproducibility has layers

AISLE distinguishes exact artifact reconstruction from statistical episode
reproduction. Code, graph, environment, injected reset state, and early
post-reset timing can be attested or compared exactly. Long-horizon simulated
physics on the supported Metal backend is chaotic; outcome claims are therefore
statistical, and a replicate independently re-satisfies the original gate
rather than promising identical per-seed verdicts. The governing details are
in [the constitution](../specs/000-constitution.md#3-platform-invariants) and
[the determinism guide](determinism.md).

### 4.3 What the evidence does not prove

Complete provenance does not establish adequate sample size. Statistical
significance does not establish practical importance. Simulation success does
not establish hardware safety. Oracle success does not establish that a
portable verifier can recognize success. One embodiment, scene, task family,
or model checkpoint does not establish generality. AISLE records these
boundaries so later experiments can target them explicitly.

### 4.4 Evidence has already changed the conclusions

AISLE's records are not merely future infrastructure. They have already
prevented several attractive but unsupported stories:

- [H1](../analysis/h1/) separated schema-valid composition from end-to-end
  launch. All measured graphs passed schema validation, yet many failed because
  their hub packages were not installed. Reporting only validation would have
  hidden the dominant systems failure.
- [H2](../analysis/h2/h2_findings.md) preserved a contaminated replication in
  which one arm could read the other arm's findings. The incident led to pinned
  campaign worktrees instead of being counted as independent confirmation.
- [H3](../analysis/h3/h3_findings.md) ended `UNDECIDED`, not “not met,” after
  the integrity audit excluded drifted library-arm cells. This is the intended
  behavior of a fail-closed evidence system. The
  [desk re-run](../analysis/h3/desk/desk_findings.md) then reached the same
  verdict for a different and more interesting reason: not hygiene, but
  **instrument design**. T1 and T4 were solved by both arms inside one
  sub-budget and T2/T3 by neither, so no tier sat in the band where an
  accumulation effect could appear. An accumulation benchmark is only
  measurable between trivial and impossible, and that band has to be located
  empirically before the campaign rather than assumed from the curriculum.
- [H4](../analysis/h4/h4_findings.md) found a lower median hot-swap iteration
  time at T0, but overlapping extremes and a small sample prohibit a broad
  significance, equivalence, or reproducibility claim.
- [Verifier fidelity](../analysis/ver6-fidelity/) showed zero false successes
  in the recorded set but a high false-failure rate. That distinction prevents
  a conservative portable verifier from being declared interchangeable with
  the oracle.
- [A6](../analysis/a6/a6_findings.md) showed that behavioral reset adds time,
  can fall back, and carries scene drift across episodes. Teleport-only curves
  hide a real component of autonomous operation.

These cases explain why evidence collection takes time: it is doing work that
the task rollout alone cannot do—detecting contamination, establishing
comparability, preserving failure denominators, and limiting the claim to what
was actually measured.

## 5. Experimental design principles

### 5.1 Preserve one interpretable contrast

For a causal comparison, freeze the task distribution, seed policy, perception
rung, reset mode, verifier, safety envelope, evidence path, and research
budget. Vary the named treatment. When practical, randomize or counterbalance
order so warm caches, simulator startup, or learning over time do not become
the treatment accidentally.

Useful controls include:

- a hand-written expert graph to expose the cost of agent composition;
- a model-light graph to expose the incremental value and cost of learned
  models;
- oracle and realistic verifiers to separate policy quality from judge error;
- parameters-only and code-authoring arms to separate tuning from invention;
- persistent-library and wiped-library arms to isolate reuse;
- hot-swap and relaunch paths to isolate runtime iteration mechanics;
- matched agent models, budgets, and context to compare research agents.

### 5.2 Define the replicate correctly

Seeded episodes are repeated samples from a fixed robot artifact. Independent
research-agent sessions are replicates of the autonomous engineering process.
Both levels matter and should be reported separately. Treating 100 episodes
from one agent-produced graph as 100 independent agent successes exaggerates
confidence in agent reliability.

### 5.3 Preserve failures

Infrastructure failures, timeouts, unlaunchable graphs, contaminated arms, and
verifier disagreement are outcomes. Excluding them silently conditions on
success. Exclusions must be rule-derived, visible, and accompanied by the raw
cell status. An underpowered or integrity-damaged campaign should end in a
scoped inconclusive result and a better next protocol.

### 5.4 State claims at the level evidence supports

Use a claim ladder:

1. **Observation:** this artifact produced these outcomes on these records.
2. **Replicated observation:** an independent run met the same predeclared
   acceptance gate.
3. **Treatment claim:** matched, admissible arms support a difference caused by
   the named intervention.
4. **Transfer claim:** the effect survived a declared change in task,
   embodiment, perception, environment, or hardware.

Do not promote an observation directly to a substrate, model-family, safety,
or sim-to-real claim.

## 6. Advanced-model research agenda

VLA, world-model, and WAM capabilities deepen AISLE's research program. They do
not replace the typed runtime, trusted verifier, guard, reset, or evidence
harness. They enter as swappable nodes or subgraphs whose contracts, costs, and
effects can be compared with classical and hybrid alternatives.

```text
camera / depth / joint state / instruction
                  |
        +---------+----------+-------------------+
        |                    |                   |
 classical pipeline       VLA policy       model-based planner
 perception + IK        action chunks       world model scores
        |                    |               candidate actions
        +---------+----------+-------------------+
                  |
       action adapter / trajectory generator
                  |
              budget guard
                  |
        Genesis | neural environment | hardware
                  |
        oracle + portable verifier + evidence
```

The graph boundary matters because model outputs are rarely identical. One VLA
may emit end-effector deltas, another joint targets, and a WAM may emit actions
plus predicted futures. Typed adapters make the action space, reference frame,
units, rate, chunk horizon, confidence, and validity interval explicit before
commands reach the guard.

### 6.1 Model roles and candidate contracts

| Capability | Inputs | Outputs | Research role |
|---|---|---|---|
| VLM reasoner/verifier | images, instruction, task context | plan/tool call or structured verdict with confidence | semantic reasoning and portable judgment, evaluated against oracle truth |
| VLA policy | synchronized images, robot state, instruction, optional history | action chunk in a declared representation plus confidence/uncertainty | learned alternative to classical perception-planning-control |
| World-model predictor | observation/history and candidate action sequence | predicted state/video/latent trajectory, reward/risk estimates, uncertainty | counterfactual evaluation, planning, and candidate screening |
| World-model environment | actions and reset/task context | contract-compatible observations and episode state | cheap neural-simulator rung below explicit physics and hardware |
| WAM | observation/history, instruction, optional candidate goal | action sequence plus a defined predictive product | joint action generation and future modeling; exact semantics must be declared per model |

“WAM” is an umbrella term rather than a stable interface. AISLE should register
what a particular model consumes and produces instead of inferring behavior
from the family name.

### 6.2 First questions to test

| ID | Question | Matched contrast | Primary outcomes |
|---|---|---|---|
| M1 | When does a learned policy add value? | classical graph vs VLA vs hybrid fallback | pass/failure mix, inference cost, latency, guard intervention |
| M2 | Does predictive planning improve action selection? | direct policy vs world-model reranking vs model-predictive control | held-out success, sample efficiency, planning latency, uncertainty calibration |
| M3 | Is a neural environment useful for screening? | candidate-policy rankings in neural environment vs Genesis, then hardware when available | rank agreement, false promotion/rejection, cost per candidate |
| M4 | Does representation survive transfer? | same task contract across perception rungs and embodiments | zero-/few-shot transfer, adapter changes, regression rate |
| M5 | Can coding agents orchestrate models effectively? | fixed model catalog with and without agent graph authorship/diagnostic tools | time to working hybrid, selection quality, repair quality, budget |
| M6 | Do learned policies remain governable? | matched policies behind the same action adapter and guard | unsafe proposals, clamps/rejections, wrong-object and collision outcomes |

For M1-M6, keep task, held-out seeds, observation access, verifier, reset,
safety limits, and evidence requirements fixed. Either normalize inference and
research compute or report both explicitly as a cost/performance frontier.
Parameter count alone is not a compute control.

### 6.3 A staged integration ladder

1. **Model-light baseline:** establish that the outer research loop and trust
   envelope work without attributing gains to a foundation model in the robot
   loop. This is AISLE's current experimental foundation.
2. **Passive and shadow inference:** record synchronized inputs and model
   predictions without granting motion authority. Validate schemas, timing,
   calibration, resource use, and failure behavior.
3. **Guarded policy comparison:** route VLA or WAM action proposals through an
   explicit adapter and the existing budget guard; compare with classical and
   hybrid policies.
4. **Predictive planning:** use a world model to rank or refine candidate
   actions while Genesis remains the executed environment and oracle scorer.
5. **Environment substitution:** screen candidates in a neural environment,
   then measure ranking and failure agreement in Genesis.
6. **Hardware grounding:** swap the environment/driver node behind the same
   topic contract, add hardware-specific interlocks and review, and evaluate
   sim-to-real claims separately.

Each stage has a fallback and a falsifiable purpose. A model may improve task
success yet fail the systems test because latency breaks the topic contract,
uncertainty is unusable, the action adapter is brittle, provenance is
incomplete, or its gain disappears under matched compute.

## 7. What success for AISLE would mean

AISLE succeeds as a research program if it can produce calibrated answers to
the central question—not only positive answers. Strong evidence might show
that agents compose typed systems reliably in some regimes but struggle with
installation, that trace-guided iteration improves held-out performance, that
evalcarded skills transfer only above a task-similarity threshold, that
hot-swap changes iteration cost without changing outcomes, or that a learned
model improves capability while creating latency or safety tradeoffs.

The highest-value artifact is therefore an **experimental operating system for
agentic robotics**: a place where classical components, VLAs, world models,
WAMs, simulators, and hardware drivers can be composed under common contracts;
where agents can improve them without owning the scorer or safety boundary;
and where every result carries enough evidence to be challenged, reproduced,
and reused.

## 8. Reading map

- [Experiment design](Project_AISLE_Experiment_Design.md): original project
  rationale, tasks, hypotheses, ablations, and phased design.
- [Experiments](experiments.md): protocols, current verdicts, and committed
  evidence locations.
- [Contributor wiki](contributor-wiki.md): source-linked architecture and
  implementation map.
- [Physical AI primer](physical-ai-primer.md): VLM/VLA/world-model/WAM concepts,
  sim-to-real, and an accessible guide to reading results.
- [Architecture](architecture.md): runtime boundaries and topic flow.
- [Determinism](determinism.md): exact and statistical reproducibility layers.
- [Numbered specifications](../specs/): normative contracts and acceptance
  requirements.
