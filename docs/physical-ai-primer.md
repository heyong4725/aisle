# Physical AI primer — the concepts behind AISLE, for newcomers

AISLE is a working, laptop-scale instance of **Physical AI
auto-research**: coding agents running the robotics research loop on
open infrastructure. This page explains the ideas a newcomer needs —
what Physical AI is, the model families (VLM, VLA, world models, WAMs),
sim-to-real — and, for each one, **where you can touch it in this
repo**. Depth: `references/Physical_AI_Unified_Report_v2.md` (the field
survey this page draws on), `Project_AISLE_Experiment_Design.md` (this
project's design), and the numbered specs.

## 1. What Physical AI is, and where AISLE sits

"Physical AI" is AI that acts in the physical world — perception,
manipulation, locomotion — where every mistake has a cost and every
trial needs a reset. The field's 2026 shape (per the reference report)
has three layers:

1. **The model layer** — policies and world models (Sections 3–4).
2. **The environment layer** — simulators, resets, verifiers, data.
3. **The agentic layer** — coding agents (Claude Code / Codex-class)
   that *conduct research* on the two layers below: NVIDIA's ENPIRE
   (agents running the full research loop on real robots, 99% pass@8 on
   GPU insertion) and ASPIRE (agents distilling debugging into a
   compounding skill library).

**AISLE lives in layer 3.** Its claim under test: the agentic loop
should run on an *open, typed dataflow substrate* (dora-rs + Genesis)
rather than a bespoke closed harness — making it auditable, safer, and
reproducible on a MacBook. The reference report's own conclusion
(§6.3): the agentic layer "elevates the middleware layer — dataflow
runtimes, skill and experiment registries, multimodal trace capture and
replay, hot-swappable nodes, Git-mediated agent coordination — from
plumbing to strategic infrastructure… runtimes designed for low-latency
dataflow and replayability (e.g., dora-rs-class frameworks) map
directly onto ENPIRE's EN/R modules." That sentence is this repo's
thesis, stated independently.

**The ENPIRE↔AISLE map** (useful when reading either):

| ENPIRE module | What it is | Where in AISLE |
|---|---|---|
| EN — Environment | auto-reset + auto-verification the agent can call | `src/aisle/reset/` (teleport service), `src/aisle/verifier/` (oracle + planogram judges) — the CON-7 **frozen set** |
| PI — Policy Improvement | agents revising policy code from evidence | agent sessions editing dataflow YAML + node code under `harness/CLAUDE.research.md` |
| R — Rollout | budgeted physical trials with full traces | `harness rollout` (HAR-1..5): seeded episodes, Arrow traces, video, tamper-evident budget ledger |
| E — Evolution | idea branches, log analysis, recipe reuse | idea tree (`harness report`), git worktrees per arm, campaign runners (`tools/campaign.py`, `tools/h3_campaign.py`) |
| ASPIRE skill library | distilled fixes as reusable skills | `harness skill register`: subgraph/node + manifest + evalcard, human-gated PR (CAP-7) |

## 2. The model families, in one pass

You will meet these acronyms everywhere; here is the lineage:

- **VLM (Vision-Language Model)** — a language model that also sees
  (answers questions about images). No actions.
- **VLA (Vision-Language-Action)** — a VLM with an action head:
  observations + instruction → motor actions (GR00T N1.x, π0, Helix).
  The 2024–25 workhorse. The critique (report §3): VLAs inherit
  internet-scale *semantics* but never explicitly model physical
  dynamics — "good at nouns, weak at verbs."
- **World model** — a model that predicts *future world states* from
  past observations and candidate actions: a learned, differentiable
  simulator. Three competing architectures share the buzzword:
  generative-pixels (Cosmos, Genie, DreamDojo), latent-space JEPA
  (LeCun's lineage), and the action-integrated form below.
- **WAM (World Action Model)** — 2026's shift (DreamZero): ONE model
  jointly predicts future video *and* actions, so the policy inherits
  the video backbone's physics priors. ~2x VLA task progress in unseen
  environments, real-time at 7 Hz. GR00T N2 is WAM-based. The expected
  endpoint is hybrid: "WAM body, VLM head."
- **Neural simulator** — a world model used *as the environment*
  (DreamDojo): action-conditioned video generation, no meshes, no
  engine; its policy *rankings* correlate with reality at r = 0.995 —
  which is why the report predicts neural sims become the field's CI
  before they become its gym.
- **System 1 / System 2** — the standard deployment decomposition: a
  slow deliberative planner (7–9 Hz VLA/WAM) over a fast low-level
  controller (100–1000 Hz). Keep this in mind when you read latency
  budgets.

**Where models sit in AISLE today: deliberately nowhere in the
pipeline.** Every scored episode so far runs classical, model-free
nodes (oracle-pose passthrough, geometric grasp planning, analytic IK,
scripted state machines, planogram-diff verification). This is the
design doc's §7.5 rule: *the loop must first work model-light so the
agentic contribution is cleanly isolated* — if a VLA did the grasping,
you couldn't attribute results to the substrate or the agents. The AI
in AISLE's runs is the **agents** (claude-fable-5, gpt-5.6-sol),
recorded in every campaign's treatment block; they write the pipelines
between episodes and never execute inside one.

**Where models will enter**, each behind the same typed topic contract
(design doc §7.5, decision 3):

1. `vlm-verifier` nodes — the first arrival: the realistic verifier
   (`decisions/ADR-realistic-verifier.md`, VER-5) puts OWLv2-class
   detection + segmentation into *scoring*, with fidelity vs. the
   oracle (VER-6) as a first-class result.
2. `vla-policy` nodes — GR00T/π0-class policies as swappable graph
   nodes, making "engineered pipeline vs. learned policy" an A/B the
   agent can run itself.
3. `world-model-env` nodes — a DreamDojo-class neural sim as *just
   another environment node* behind the obs/cmd contract (see §3).

## 3. Sim-to-real, real-to-sim, and the environment ladder

**Sim-to-real** is the gap between simulated and physical success. The
canonical cautionary datum (report §6.1): all three ENPIRE agents
solved Push-T in simulation; two of three failed on the real robot —
friction, dynamics, and sensor noise the sim didn't carry. The
classical toolkit: **domain randomization** (vary textures, lighting,
friction, poses so policies can't overfit sim quirks — our scene
builders expose these toggles), photoreal rendering (Isaac-class), and
contact-parameter tuning (our `physics.toml` is a week of exactly
that). **Real-to-sim** is the reverse direction: building the sim from
reality — digital twins from smartphone capture (NuRec), and at the
frontier, *learning* the simulator from video (DreamDojo's 44K hours of
egocentric human video; see also EgoScale's R²=0.998 scaling law of
dexterity vs. human-video hours — the result that moved the field's
data axis from robot-hours to human-hours).

AISLE is sim-first and says so honestly (design doc §10.2 lists it as
the top "con"). Its mitigations are structural, designed so nothing in
the loop assumes sim privileges:

- **The perception ladder** (L0 oracle poses → L1 ground-truth
  segmentation → L2 real pixels): results are reported per rung, so
  "solved with oracle perception" can never masquerade as "solved."
- **The topic contract** (`SPEC 010`, CONTRACT.md discipline): the
  bridge's obs/cmd topics are the *hardware driver interface* — Phase 4
  sim-to-real is a driver-node swap, not a rewrite.
- **Behavioral reset** (SPEC 040, phase 2): the robot physically
  re-shelves the box — parity with what a real deployment must do;
  ablation A6 measures what teleport-reset hides.
- **Verifier fidelity** (VER-6): the camera-based realistic verifier is
  scored against the oracle — the number that says whether this loop
  ports to a physical desk.
- **The three-tier environment ladder** (§7.5): neural sim (cheap
  screening) → Genesis (physics-verified iteration) → hardware
  (grounding), with *graph identity preserved* across tiers because the
  environment is just a node. Tier-agreement (does neural-sim ranking
  match Genesis match reality — DreamDojo's r=0.995 question) becomes
  measurable inside one runtime.

## 4. Why this repo is an education in auto-research method

The rarest thing AISLE offers a learner is not the code — it is the
**recorded practice of rigorous agentic research, including the
failures**. The committed record is the curriculum:

- **Pre-registered hypotheses**: every agent idea is logged with an
  expected effect *before* running (`harness report`, HAR-8) — the
  idea tree is a lab notebook the harness enforces.
- **Held-out evaluation**: agents iterate on dev seeds; the runner
  scores untouched held-out seeds after the session. See H3's W/S3,
  where 1.0 on self-selected dev seeds collapsed to 0.0 held-out —
  and the analysis of *why* (`analysis/h3/`).
- **Contamination discipline**: the H2 codex arm once read the
  committed findings of the experiment it was replicating — the run is
  kept, labeled invalid, as the lesson (`analysis/h2/`); replication
  arms now pin commits predating any same-experiment analysis.
- **Integrity flags derived from records, never asserted**:
  `tools/h3_analysis.py` computes `wipe_leak`/`residue_leak`/
  `holdout_partial` from the artifacts; flagged cells are excluded
  from verdicts and rerun under new ids.
- **Environment attestation** (ADR-24): a run's installed environment
  is verified against the lock (fingerprint in CON-5's tuple,
  fail-closed post-run audit) — "it worked on my machine" is not an
  admissible result.
- **Honest negative results**: H1's headline is that zero-shot
  composition *missed* its target (and exactly why); H3's is NOT MET
  under its budgets, with the budget confound stated rather than
  buried. Negative results with clean provenance are publishable; that
  is the point.
- **Structural safety over behavioral safety** (H5): motion-class
  gating, an unroutable oracle topic, frozen verifiers, trust-anchored
  hot-swap — and the honest finding that the guarantee's *retail*
  analogue (`extra_item`) is verifier-detected, not guard-gateable.

A suggested learning path: (1) `getting-started.md` and run the expert
graph; (2) read this page against report §§3–6; (3) read one campaign
transcript end-to-end (`runs/` after any session — or the annotated
findings in `analysis/h1/`); (4) trace one integrity mechanism from
spec to test to enforcement (e.g. VAL-5 motion gating, or ADR-21's
self-verifying checker); (5) read `analysis/h3/h3_findings.md` as a
case study in what disciplined *inconclusive* science looks like.

## 5. Further reading

- `references/Physical_AI_Unified_Report_v2.md` — the July 2026 field
  survey (WAMs, EgoScale, DreamDojo, ENPIRE/ASPIRE, the competitive
  landscape) this page cites throughout.
- ENPIRE (arXiv 2606.19980) and the ASPIRE release notes — the systems
  AISLE reproduces on open middleware (design doc §10 for the honest
  comparison).
- DreamZero / "World Action Models are Zero-shot Policies"
  (arXiv 2602.15922) — the WAM paradigm.
- *Code as Policies* (Liang et al.) and *Voyager* (Wang et al.) — the
  code-as-policy and skill-library ancestors of `skills/`.
- dora-rs (`dora-rs.ai`) and Genesis (`genesis-world.readthedocs.io`)
  — the substrate.
