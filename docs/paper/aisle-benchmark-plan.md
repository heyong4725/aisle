# AISLE benchmark paper — gap-closure and execution plan

Date: 2026-08-31
Status: DRAFT; protocols and power targets require owner ratification before
confirmatory runs
Companion review: [`aisle-review-verdict.md`](aisle-review-verdict.md)
Project-level manuscript: [`aisle-paper.md`](aisle-paper.md)
Project-quality roadmap: [`../project-quality-roadmap.md`](../project-quality-roadmap.md)

## Working title

> **AISLE: An Auditable Benchmark for Coding-Agent Robot-System Engineering
> with Typed Dataflows**

## 1. Paper boundary

This is a second, focused paper. It is not a page-fitted version of the broad
AISLE technical narrative.

The paper asks one primary question:

> **Do typed capability contracts and static dataflow validation improve coding
> agents' ability to build and repair robot systems, relative to an
> equal-capability monolithic engineering interface?**

It has one secondary question:

> **Do typed runtime evidence and probes improve an agent's ability to localize
> and repair hidden faults in a running robot system?**

The proposed contributions are limited to:

1. An open, auditable benchmark whose experimental unit is an autonomous coding
   session operating a robot-system repository under frozen evaluation.
2. A controlled typed-dataflow versus monolithic-interface experiment.
3. A blinded live-fault diagnosis and repair benchmark, including no-fault
   controls.
4. A layered reproducibility and safety-boundary evaluation, with a physical
   validation cell when targeting a physical-AI main track.

VLM judge experiments, the SmolVLA dose study, granular physics, the v0
surrogate, reset ablations, fleet scaling, and historical campaign postmortems
remain in the technical report and supplement. They are not headline evidence
for this paper.

## 2. Claim discipline

The confirmatory paper will make only claims mapped to a completed experiment.

| Candidate claim | Evidence required | Claim if the evidence is absent |
|---|---|---|
| Typed dataflows improve engineering success | Repeated typed-vs-monolithic control | AISLE enables auditable measurement; no superiority claim |
| Actionable validation reduces repair effort | Typed generic-error or no-hint comparator | Descriptive validator behavior only |
| Typed evidence improves fault operations | Hidden faults, logs-only comparator, no-fault controls | H6 is a feasibility case study |
| Guard prevents unsafe motion | Explicit threat model, adversarial and held-command guard ablations | Graph-topology gating and observed clamps only |
| Wrong-object delivery is prevented | Identity-aware authorization intervention | Zero observed wrong-object events only |
| Results are reproducible | Independent-machine reproduction | Treatment is hash-attested only |
| Result concerns physical robots | Physical validation | Simulation-scoped robot-system benchmark |

Negative results remain publishable. If the monolithic condition matches or
beats typed dataflows, the benchmark has still answered its primary question.
No post-result relabeling of the estimand is allowed.

## 3. Workstream A — benchmark contract and fair control

### A1. Define the experimental unit

- Primary unit: one fresh, isolated coding-agent session.
- Nested observations: authored artifact, development rollouts, held-out seeds,
  and episode outcomes.
- Agent-session variation and task-seed variation must not be pooled as if they
  were the same source of uncertainty.
- A session receives one pinned model identity, prompt, repository treatment,
  budget, and hidden evaluation set.

### A2. Build the equal-capability monolithic condition

The monolithic arm must expose the same robot primitives, observations, frozen
guard, reset, verifier, budgets, and held-out scorer as the typed arm. The agent
edits one orchestration module or script rather than manifests and dataflow YAML.
It may not receive less robotics functionality merely because it lacks the
registry.

The treatment difference should be as narrow as practical:

| Surface | Typed arm | Monolithic arm |
|---|---|---|
| Robot primitives | Same pinned implementations | Same pinned implementations |
| Environment/scorer/guard | Same frozen artifacts | Same frozen artifacts |
| Composition form | Manifest-resolved dora graph | One orchestration module/script |
| Interface checking | Static schema/topology validation | Ordinary language/runtime checks only |
| Runtime evidence | Same base logs/traces for the primary build test | Same base logs/traces |
| Budget and held-out evaluation | Identical | Identical |

Before use, a human-authored expert solution in both treatments must achieve
equivalent held-out performance and comparable access to observations and
actuation. This is a fairness gate, not experimental data.

### A3. Isolate the teaching-hint mechanism

If budget permits, add a third condition:

- `typed-full`: stable error codes plus actionable alternatives and fixes.
- `typed-generic`: same type and topology checks, but generic non-teaching
  messages.
- `monolithic`: equal-capability script interface.

This separates the value of static constraints from the value of error-message
pedagogy. If the third arm would make the confirmatory study underpowered, keep
the primary two-arm comparison and study hint quality separately as exploratory.

### A4. Contamination and treatment controls

- Start every arm from a commit predating all same-experiment findings.
- Remove analysis reports, prior deliverables, and hidden fault definitions from
  the agent-visible tree.
- Pin model, CLI, runtime binary, API, prompt, tool permissions, environment
  fingerprint, and credential/session configuration.
- Randomize condition order in temporal blocks to reduce model-service and host
  load confounds.
- Capture `tokens_new`, output tokens, tool calls, wall time, and estimated API
  spend. Do not equate vendor tokenizers with a common physical unit.
- Audit agent-visible context after each session.

**A completion gate:** both treatments pass expert parity, confinement, frozen
evaluation, and artifact-retention tests before any pilot session begins.

## 4. Workstream B — primary causal experiment

### B1. Tasks

Use two deliberately different tasks:

1. **Composition task:** a short T1-class build with installed capabilities and
   non-oracle perception. This measures schema validity, launchability, and
   working-system rate without repeating H1's missing-package confound.
2. **Engineering task:** a preflight-calibrated task in the empirically
   reachable-but-nontrivial band, likely a T2-derived condition. It must leave
   room for both failure and improvement under the session budget.

T1 with oracle pose is not sufficient as the only task. T3 should not be used
until a pilot shows that both treatments have nonzero but nonsaturated success.

### B2. Outcomes

Pre-register one primary outcome per experiment:

- Composition: proportion of sessions producing a launching system that
  achieves the held-out acceptance threshold within the fixed budget.
- Engineering: session-level held-out score at budget, with time-to-first
  accepted system as a secondary survival outcome.

Secondary measures:

- first-validate validity;
- launch rate and failure class;
- validate/fix or run/fix cycles;
- held-out pass rate over a materially larger seed set;
- tokens, output tokens, wall time, tool calls, and simulator work;
- guard interventions and semantic failure classes;
- human audit score for artifact legibility, using a blinded rubric if retained.

### B3. Sample size and analysis

- Run an unscored infrastructure pilot first. Pilot sessions may calibrate task
  difficulty and estimate variance but may never enter confirmatory tables.
- Freeze an immutable protocol and analysis plan after the pilot.
- Use a formal power analysis for the smallest effect worth detecting. Until
  that analysis exists, use **10 independent sessions per condition as a floor,
  not a target**; H1's 20 sessions per agent is the stronger precedent.
- Use at least 32 held-out seeds per final artifact unless power analysis
  requires more. Seeds are paired across conditions but kept hidden from agents.
- Estimate session-level treatment effects with confidence intervals. Episode
  uncertainty must be clustered within session/artifact.
- Predefine equivalence margins before making an equal-quality cost claim.
- Report all sessions, infra exclusions, and exclusion reasons in a CONSORT-like
  flow diagram.

### B4. Models

Use at least two independently supplied coding-agent systems. Record exact
served identities and CLI revisions. The paper's primary effect is the
interface treatment averaged or stratified across agents, not a one-session
vendor ranking.

**B completion gate:** the registered analyzer regenerates all primary tables,
confidence intervals, and the session flow diagram from retained raw records.

## 5. Workstream C — blinded live-fault benchmark

### C1. Fault bank

Create a versioned fault bank outside every agent worktree. It should contain:

- perception, decision, motion, clocking, schema/metadata, and runtime faults;
- multiple severities calibrated in pilot runs;
- intermittent as well as persistent faults;
- at least two coupled-fault cases;
- no-fault controls;
- faults whose repair requires a small novel code change, not only restoration
  from a reference graph.

The experiment controller selects a fault by seed and injects it without putting
the menu, injector ledger, or repaired source in the agent-visible repository.
Faults must produce measurable degradation without creating a semantic hazard
that the safety protocol forbids.

### C2. Conditions

- `typed-evidence`: episode taxonomy, typed topic probes, node manifests, guard
  statistics, and validated graph operations.
- `logs-only`: equivalent base logs and outcomes, but no typed probe/schema
  assistance.
- Optional exploratory arm: `typed-evidence-no-hints`.

Both conditions receive the same repair authority and the same frozen scorer.

### C3. Pre-registered outcomes

- Correct node or component localization, submitted before repair.
- Correct fault-class localization.
- Validated repair success.
- Post-repair held-out recovery with confidence interval.
- Time, tokens, probes, relaunches, and regressions.
- False-alarm and unnecessary-mutation rate on no-fault controls.
- Safety events and guard interventions.

Run repeated independent sessions per fault family and condition. A 3/3
existence result is retained as historical motivation, not pooled into the new
confirmatory result.

**C completion gate:** fault identities remain absent from audited agent
transcripts and worktrees; the no-fault false-alarm rate and localization
confusion matrix are reported beside repair success.

## 6. Workstream D — safety claim repair

### D1. State the threat model

Specify what the agent can write, execute, import, open, and communicate with;
which process owns actuation; which credentials or sockets it can access; and
which attack classes are out of scope. Replace `unbypassable` with a property
whose boundary is mechanically testable.

### D2. Separate safety properties

1. **Topology:** every declared command path to the bridge traverses the guard.
2. **Kinematics:** unsafe proposed commands are clamped or refused.
3. **Semantic outcome:** the verifier detects wrong-object entry; observed event
   counts are empirical and do not prove prevention.

### D3. Experiments

- Expand adversarial bypass tests to direct imports, alternate paths, malformed
  metadata, process/network side channels allowed by the threat model, and
  guard failure modes.
- Use a held-command guard-on/guard-off ablation so plan variation cannot explain
  changes in kinematic violations.
- Generate an exact safety-exposure table: episodes, manipulation attempts,
  deliveries, clamps, workspace events, collisions, and wrong-object events.
- Report a zero-event upper confidence bound rather than an unqualified zero.
- Treat an identity-aware authorization layer as separate future work unless it
  is implemented and ablated.

**D completion gate:** every safety sentence in the paper maps to a named
topology test, intervention experiment, or exposure statistic.

## 7. Workstream E — physical and non-oracle validation

### E1. Non-oracle gate

At least one positive primary-paper result must use ordinary sensor-derived
perception. The perception system and realistic verifier need separate fidelity
measurements; an imperfect realistic verifier must not silently become the
ground-truth scorer.

### E2. Hardware gate

For a physical-AI main-track submission:

- run the selected typed and control artifacts behind the same SO-101 driver
  contract;
- use a fixed calibration protocol and a physical scorer whose errors are
  measured against an independent audit;
- include enough trials to report uncertainty, not only a demonstration clip;
- include at least one hardware fault-diagnosis cell if live operation is a
  headline contribution;
- report every human intervention and reset.

If hardware is unavailable, explicitly target a simulation/system/demo venue
and remove physical-deployment claims. Hardware preparedness is not a result.

**E completion gate:** the paper contains either a physical result with raw
evidence or an explicit simulation-scoped title, abstract, and conclusion.

## 8. Workstream F — independent reproduction and release

- Preserve every confirmatory raw session, transcript, run manifest, episode
  stream, analyzer input, and exclusion record.
- Run the primary analysis from a clean clone with one documented command.
- Reproduce selected primary cells on a second machine/operator environment.
- Report reproducibility by layer: exact generated artifacts, timing/cadence,
  physics tolerance window, and statistical outcomes.
- Measure lockstep wall-time cost.
- Archive code and evidence under a DOI-bearing immutable release; create a
  separate anonymized archive for double-blind review.
- Include licenses, model access requirements, expected compute/time, and a
  machine-readable artifact manifest.

**F completion gate:** an independent operator can regenerate the paper's main
tables and figures without access to the original campaign machine or gitignored
state.

## 9. Protocol governance

Each new confirmatory experiment requires its own accepted ADR before scored
runs. The ADR must include:

- primary estimand and experimental unit;
- treatment definitions and hashes;
- randomization/blocking plan;
- inclusion/exclusion and infra-rerun rules;
- power analysis and stopping rule;
- hidden seed/fault handling;
- safety envelope;
- analyzer command and expected schema;
- amendment policy.

Pilot amendments are expected. Once the confirmatory protocol is frozen, any
material amendment ends the current confirmatory study and starts a newly
versioned one. Deposit the frozen protocol in an externally timestamped archive
before scoring.

Implementation that changes normative behavior must follow the repository's
spec-change rules; this planning document does not modify a requirement.

## 10. Paper outline and page budget

Target: eight-page main paper, excluding references, with at most five main
figures/tables.

1. **Introduction and contributions** — one question, three contributions,
   explicit simulation/hardware scope.
2. **Benchmark definition** — experimental unit, agent action space, frozen
   evaluator, threat model.
3. **AISLE substrate** — typed registry, validator, guard boundary, evidence
   chain; one combined architecture figure.
4. **Causal experiment** — treatments, parity gate, randomization, power, and
   primary metrics.
5. **Fault benchmark** — hidden injector, evidence conditions, no-fault controls.
6. **Results** — session-level effects and intervals; one failure-flow figure,
   one treatment-effect figure, one diagnosis confusion/timeline figure.
7. **Physical/reproducibility validation** — concise table.
8. **Limitations, related work, and conclusion**.

The abstract should be 180–220 words and contain only the benchmark, treatment,
primary causal result, fault result, and scope limitation. Avoid project-history
language such as `with receipts`, `honest negative`, and `the instrument
corrected itself` in the scientific claims.

## 11. Related-work revision

At minimum, position against:

- ENPIRE: real-hardware agentic policy-improvement loop;
- ASPIRE: agent-authored reusable robot skills and transfer;
- RHO: repository-as-policy optimization and established robot benchmarks;
- RigorBench: coding-agent process-discipline measurement;
- Physical Agentic AI: typed robot skills and deterministic runtime enforcement;
- Code as Policies and Voyager;
- typed robotics/dataflow middleware, including ROS 2 and dora-rs;
- runtime assurance, shielding, and safety-monitor architectures;
- reproducible robotics benchmark and artifact-evaluation practice.

The novelty statement should be comparative and narrow: AISLE's intended
distinction is the coding **session as the experimental unit**, combined with
typed robot-system composition, frozen evaluation, evidence provenance, and a
controlled interface ablation.

## 12. Execution order

| Gate | Work | Depends on | Exit criterion |
|---|---|---|---|
| G0 | Ratify paper boundary and claims | review verdict | primary and secondary questions frozen |
| G1 | Build and test fair monolithic control | G0 | expert parity and confinement pass |
| G2 | Build hidden fault bank and logs-only condition | G0 | blinding and no-fault tests pass |
| G3 | Run unscored pilots and power analysis | G1, G2 | reachable task band and sample sizes frozen |
| G4 | Deposit protocols and run causal study | G3 | analyzer-derived treatment results complete |
| G5 | Run blinded fault study | G3 | localization, repair, and false-alarm results complete |
| G6 | Repair safety claims and run adversarial ablations | G1 | threat-model evidence table complete |
| G7 | Non-oracle, hardware, and independent reproduction | G4–G6 | physical/scope and artifact gates satisfied |
| G8 | Draft and internally red-team the second paper | G4–G7 | all headline sentences trace to evidence |

## 13. Stop/go rule

Proceed to a physical-AI main-track submission only when G1–G8 are complete.
Do not fill missing gates with historical single-session evidence. If hardware
or confirmatory replication cannot be completed, release the benchmark and
technical report, target an appropriate systems demo or workshop, and retain
the narrower claims. A negative typed-dataflow result is scientifically valid;
an unrun control is not.
