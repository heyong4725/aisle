# Physical AI primer — the concepts behind AISLE, for newcomers

AISLE is a working, laptop-scale instance of **Physical AI
auto-research**: coding agents running the robotics research loop on
open infrastructure. This page explains the ideas a newcomer needs —
the roles models play in a robot system, sim-to-real, and how to read
this repo's results — and, for each, **where you can touch it here**.
Depth: `references/Physical_AI_Unified_Report_v2.md` (a dated industry
snapshot — read its PROVENANCE and calibration notes first),
`Project_AISLE_Experiment_Design.md`, and the numbered specs. Primary
papers are linked where cited; "the paper reports" marks
preprint/vendor results.

## 1. What Physical AI is, and where AISLE sits

"Physical AI" is AI that acts in the physical world — perception,
manipulation, locomotion — where every mistake has a cost and every
trial needs a reset. Three layers organize the field:

1. **The model layer** — policies and predictive models (Section 2).
2. **The environment layer** — simulators, resets, verifiers, data.
3. **The agentic layer** — coding agents (Claude Code / Codex-class)
   that *conduct research* on the two layers below. NVIDIA's
   [ENPIRE](https://arxiv.org/abs/2606.19980) (agents running the full
   research loop on real robots; the paper reports up to 99% rollout
   success — with up to eight adaptive retries — on bounded dexterous
   tasks like Push-T, pin-box organization, and zip-tie cutting, and
   separately reports high-success transfer to GPU insertion) and
   [ASPIRE](https://arxiv.org/abs/2607.00272) (agents distilling
   debugging into a compounding skill library; the paper reports
   dual-arm handover improving 20%→92% through iterative repair).

**AISLE lives in layer 3.** Its claim under test: the agentic loop
should run on an *open, typed dataflow substrate* (dora-rs + Genesis)
rather than a bespoke closed harness — auditable, safer, reproducible
on a MacBook. The industry snapshot's §6.3 argues the same middleware
thesis (see its provenance note for sourcing status).

**AISLE's two nested loops** — the distinction this whole repo turns
on:

```
OUTER (research) loop, between episodes — the agent's loop:
  agent proposes a change (graph YAML / node code / params)
    → validator type-checks the graph      (SPEC 060; the compile loop)
    → safety guard constrains capabilities (SPEC 080; motion gating)
    → rollout executes seeded episodes     (HAR-1..5)
    → verifier scores each episode         (SPEC 040; frozen)
    → traces + manifest are committed      (Arrow, video, attestation)
    → agent reads evidence and revises     (idea tree, HAR-8)

INNER (runtime) loop, within an episode — the robot's loop:
  observations flow through the dataflow graph → commands, at
  topic-contract rates, with no agent in the path.
```

The agent never executes inside an episode; the pipeline never
improves itself mid-run. Keep this separation in mind whenever you
read "the agent did X."

**The ENPIRE↔AISLE map** (useful when reading either):

| ENPIRE module | What it is | Where in AISLE |
|---|---|---|
| EN — Environment | auto-reset + auto-verification the agent can call | `src/aisle/reset/` (teleport service), `src/aisle/verifier/` (oracle + planogram judges) — the CON-7 **frozen set** |
| PI — Policy Improvement | agents revising policy code from evidence | agent sessions editing dataflow YAML + node code under `harness/CLAUDE.research.md` |
| R — Rollout | budgeted physical trials with full traces | `harness rollout` (HAR-1..5): seeded episodes, Arrow traces, video, tamper-evident budget ledger |
| E — Evolution | idea branches, log analysis, recipe reuse | idea tree (`harness report`), git worktrees per arm, campaign runners (`tools/campaign.py`, `tools/h3_campaign.py`) |
| ASPIRE skill library | distilled fixes as reusable skills | `harness skill register`: subgraph/node + manifest + evalcard, human-gated PR (CAP-7) |

## 2. Model roles, not a model lineage

The acronyms (VLM, VLA, world model, WAM) name **roles and families
that can coexist in one system** — not stages of an evolution. The
useful question for any robot system is: *which component answers
which question?*

| Role | Question it answers | Common implementations | AISLE status |
|---|---|---|---|
| Perception / reasoning | What is present; what does the instruction mean? | detector/segmenter, VLM | L0/L1/L2 perception ladder implemented (oracle poses, segmentation+depth, RGB identity + sensor-depth geometry); VLM reasoning planned |
| Action policy | What action (or action chunk) next? | state machine, VLA, WAM | classical graph nodes now; VLA/WAM nodes planned |
| Predictive dynamics | What follows if action `a` is taken in state `s`? | explicit simulator, state-space or video world model | Genesis (explicit sim) now; learned world-model environment planned |
| Execution / control | How do targets become safe high-rate commands? | IK, trajectory generation, servo loops | **implemented** (`ik-trajectory`, drivers, guard) |
| Verifier / reward | Did the task succeed; how did it fail? | oracle rules, detector+rules, VLM judge | oracle/planogram AND realistic (detector+rules) **implemented**, oracle-fidelity measured; VLM judge planned |
| Research outer loop | What should change between rollouts? | coding agent (ENPIRE-like) | **implemented and under experiment** (H1–H3) |
| Trust envelope | Which changes/runs are admissible? | validator, motion guard, frozen set, attestation | **implemented**, with documented limits (§5) |

Calibrated definitions for the families you'll meet:

- **VLM (Vision-Language Model)** — a language model that also sees.
  It can produce *plans and tool calls*; what it normally lacks is a
  native continuous motor-action output.
- **VLA (Vision-Language-Action)** — a VLM with an action head:
  observations + instruction → actions (GR00T N1.x, π0, Helix). The
  2024–25 workhorse for the *action policy* role. The critique (see
  [DreamZero](https://arxiv.org/abs/2602.15922)): VLAs inherit
  internet-scale semantics without explicitly modeling physical
  dynamics.
- **World model** — a learned predictive *transition model*: given
  state/observations and a candidate action, predict what follows. It
  need not be pixel-generative, differentiable, or an interactive
  simulator — those are particular implementations (generative-video
  models like Cosmos/Genie; latent-space JEPA models; and others).
- **WAM (World Action Model)** — a family coupling action generation
  with predictive modeling. DreamZero's formulation (the paper
  reports ~2x VLA task progress in unseen environments at 7 Hz
  closed-loop) jointly predicts future video and actions in one
  model — that is DreamZero's design, not a universal WAM definition.
- **Neural simulator** — a *use mode* of a world model: the model as
  the environment. [DreamDojo](https://arxiv.org/abs/2602.06949) is
  the video-generative example ("no meshes, no engine" describes
  DreamDojo specifically; hybrid neural/explicit simulators also
  exist). Its paper reports policy-*ranking* correlation with real
  outcomes of r = 0.995 on a fruit-packing checkpoint-ranking
  experiment — a strong result on its evaluated scenes, not a general
  reality-correlation guarantee (the paper itself notes optimistic
  absolute success rates).
- **Control hierarchy** — deployed systems layer deliberation (slow),
  policy/action chunks (mid-rate), and servo/safety loops (fast).
  "System 1 / System 2" is an analogy some systems use for this — a
  useful mental model, not a standardized architecture.
- **Scaling laws arrive**: [EgoScale](https://arxiv.org/abs/2602.16710)
  reports a near-perfect log-linear fit (R² = 0.9983) between
  egocentric human-video hours and held-out human-action validation
  loss — a loss that strongly tracks downstream robot performance.
  (Precision matters: the R² is on the loss fit, not directly on
  "dexterity vs. video hours.")

**Where models sit in AISLE today: deliberately nowhere in the
runtime loop.** Every scored episode so far runs classical, model-free
nodes (oracle-pose passthrough, geometric grasp planning, analytic IK,
scripted state machines, planogram-diff verification). This is design
doc §7.5's rule: *the loop must first work model-light so the agentic
contribution is cleanly isolated*. The AI in AISLE's runs is the
**agents** (claude-fable-5, gpt-5.6-sol — pinned in every campaign's
treatment record); they write pipelines between episodes and never
execute inside one.

**Where models enter** — item 1 is implemented; items 2 onward remain
planned — each behind the same typed topic contract (design doc §7.5):

1. **The realistic verifier** — **[implemented]** `src/aisle/verifier/
   realistic.py` (VER-5, ADR `decisions/ADR-realistic-verifier.md`): a
   detector + segmentation + *rules* pipeline (OWLv2 detection,
   depth-assisted judgment, CPU-pinned for bit-identical replay)
   scoring episodes from camera pixels. Its fidelity vs. the oracle
   (VER-6) is measured, not pending: the current VER-13 fusion recomputes
   the same 31 episodes at **0.45** agreement, **0.00** false SUCCESS
   (0/6), and **0.68** false FAIL (17/25). The preserved first,
   pre-amendment finding was 0.29 / 0.00 / 0.88
   (`analysis/ver6-fidelity/`; recomputation in SPEC 040 VER-13) — still
   conservative, and not yet interchangeable with the oracle. NOT a VLM —
   the ADR records that an open-vocabulary detector alone cannot judge the
   task.
2. `vlm-verifier` nodes (later, optional) — a Cosmos-Reason-class VLM
   as an ALTERNATIVE judge alongside the detector+rules verifier; the
   design compares both verifiers' fidelity.
3. `vla-policy` nodes — GR00T/π0-class policies as swappable graph
   nodes, making "engineered pipeline vs. learned policy" an A/B the
   agent can run itself.
4. `world-model-env` nodes — a DreamDojo-class neural sim as *just
   another environment node* behind the obs/cmd contract (see §3).

## 3. Sim-to-real and real-to-sim

**Sim-to-real** is the gap between simulated and physical success.
The cautionary datum from ENPIRE: all three harnessed agents solved
Push-T in simulation; two of three initially failed on the real
robot. The gap has many distinct sources, each with its own tool —
domain randomization is *one* mitigation, not the definition:

| Gap source | Mitigation family | In AISLE |
|---|---|---|
| Contact/friction & actuator dynamics | system identification, parameter calibration | **[implemented]** `physics.toml` — hand-tuned contact parameters, versioned, never inline |
| Visual distribution shift | domain randomization, photoreal rendering | **[implemented]** DR toggles in the scene builders (poses, lighting, textures, friction, camera jitter); exercised by tests, not yet by scored L2 runs |
| Perception difficulty conflated with loop capability | staged perception ladder | **[implemented]** the ladder is L0 oracle object poses → L1 ground-truth segmentation with estimated poses (`segmented-pose`, `graphs/expert_t1.yaml`) → L2 RGB-only identity with same-stamp ordinary sensor depth for metric geometry (`l2-pose`, `graphs/expert_t1_l2.yaml`). The rung rides the GRAPH, so the graph hash attests which pose source a result used; `harness rollout --perception` asserts it and refuses a mismatch (TC-9, VAL-8) |
| Embodiment mismatch | contract-first driver abstraction | **[implemented]** the topic contract (SPEC 010, `src/aisle/topics.py`): obs/cmd topics are the hardware driver interface — Phase 4 sim-to-real is a driver-node swap, not a rewrite; `--embodiment` swaps profiles with zero YAML edits |
| Observation/action latency & rates | rate-typed contracts, latency classes | **[implemented]** manifest `rate_hz`/`latency_class` fields checked by the validator |
| Reset parity | behavioral (physical) reset | **[planned — SPEC 040 phase 2]** the robot re-shelving the box; ablation A6 measures what teleport hides. Today only teleport runs (`--reset behavioral` raises NotImplementedError by design) |
| Verifier portability | verifier-fidelity measurement | **[implemented, measured]** `src/aisle/harness/fidelity.py` compares the camera-based verifier against the oracle; current VER-13 recomputation over 31 episodes is agreement 0.45, false SUCCESS 0.00, false FAIL 0.68. The preserved pre-amendment finding is 0.29 / 0.00 / 0.88 (`analysis/ver6-fidelity/`; SPEC 040 VER-13) |
| Sim-specific physics exploits | cross-simulator checks | **[planned]** the MuJoCo grasp micro-benchmark cross-check (design doc §7) |

**Real-to-sim** is the reverse direction: building the sim from
reality — digital twins from capture (NuRec-class), and at the
frontier, *learning* the simulator from video (DreamDojo's training
corpus is ~44K hours of egocentric human video). AISLE's planned
three-tier environment ladder (§7.5) — neural sim → Genesis →
hardware, with graph identity preserved because the environment is
just a node — is where that direction lands here; no
`world-model-env` node exists yet.

## 4. How to read an AISLE result

Robotics results are easy to over-read. The repo's findings follow
rules worth learning as method:

- **Know the estimand.** An "agent success rate" here is end-to-end:
  compose → validate → launch → succeed. Conditioning on "graphs that
  launched" silently deletes the dominant failure mode (that exact
  correction is on the record in `analysis/a1/`, PR #70 review).
- **Episodes are not replicates.** 8 seeded episodes measure one
  system's pass rate; independent agent *sessions* are the replicates
  of the research process. Single-session cells carry wide variance
  (see `analysis/h3/`'s S1 pair: 0.375 vs 0.5 from the same
  condition).
- **Dev vs held-out.** Agents iterate on dev seeds; the runner scores
  untouched held-out seeds afterwards. Dev numbers never headline. A
  cautionary illustration: the H3 W/S3 record went 1.0 on
  self-selected dev seeds → 0.0 held-out — but note that cell is
  **contamination-flagged (`wipe_leak`) and excluded from the formal
  verdict**; it illustrates dev-seed selection bias without being
  admissible evidence for anything else.
- **Attestation proves environment identity, not statistical
  validity.** ADR-24's fingerprint says *what ran where*; it says
  nothing about whether n=8 supports your conclusion.
- **"NOT MET under these budgets" is not disproof.** H3's verdict is
  explicitly budget-scoped: the campaign could not distinguish
  "libraries don't help" from "0.75M tokens is below the scenario's
  entry cost." Negative results with clean provenance and stated
  confounds are the product, not the failure.

## 5. The trust envelope: four different questions

"Is this safe/valid?" decomposes into four layers that answer
different questions — don't blend them:

1. **Functional safety** — can the robot hurt something *right now*?
   The budget guard (SPEC 080) interposes on every motion command:
   joint/velocity/workspace clamps, episode timeouts. Structural: the
   validator refuses graphs where motion bypasses it (VAL-5), and
   live hot-swap refuses to touch trust anchors (HAR-10).
2. **Task judgment** — did the task actually succeed? Frozen
   verifiers (oracle and realistic now; VLM planned) own this; the
   unroutable `oracle_state` topic (VAL-6) keeps ground truth out of
   policies. Honest limit on the record: the guard cannot gate
   `extra_item` (picking up a neighbor item) — that's
   verifier-detected only (`analysis/a1/`, H5 discussion).
3. **Experimental integrity** — is the *evidence* clean? The CON-7
   frozen set (hash-checked at launch), held-out seeds, idea-tree
   pre-registration (HAR-8), commit pinning against contamination
   (the H2 lesson), and record-derived flags (`wipe_leak`,
   `residue_leak`, `holdout_partial`) that exclude cells from
   verdicts (`tools/h3_analysis.py`).
4. **Provenance / security** — did what we *think* ran actually run,
   where? Trusted-baseline gates verify the frozen tree AND the
   installed environment against the lock (ADR-21/ADR-24, the
   self-verifying checker), with a **fail-closed post-run inventory
   audit for trusted campaign runs**. Local dev runs record their
   weaker status (`post_run_audit: null`) and skip the expensive
   audit — they are NOT interchangeable with trusted campaign
   evidence.

ADR-21 states the boundary honestly: an agent with a shell can bypass
anything local — the enforceable properties are refusal-by-default
plus tamper *evidence* in artifacts, which the audits check.

## 6. AISLE today vs. not yet (one box)

**Today (runnable in this repo):** Genesis as the only environment;
classical model-free pipelines; the full L0/L1/L2 perception ladder;
oracle/planogram verifiers AND the realistic (detector+rules) verifier
with a measured oracle-fidelity number; teleport reset; the full
agentic outer loop (validate → guard → rollout → verify → traces →
idea tree) with campaign runners; typed contracts + registry + skill
registration; Arrow/video traces; the integrity and attestation gates
of §5.

**Not yet (design/spec/ADR only):** any physical-robot evidence; the
VLM verifier; behavioral reset; VLA/WAM policy nodes; a
neural-simulator environment; the MuJoCo cross-check.

Current verdicts and their qualifications live in one place — [the
README status table](../README.md#status). This primer explains
concepts; it deliberately does not restate experiment results.

## 7. A learning path

1. `getting-started.md`; run the expert graph.
2. This page against the industry snapshot's §§3–6 (provenance note
   first).
3. Read one campaign's findings end-to-end as a case study:
   `analysis/h1/` (a clean negative with a single dominant mechanism),
   `analysis/h2/` (a met hypothesis *plus* the contamination lesson),
   `analysis/h3/` (disciplined inconclusiveness: flags, exclusions,
   budget confounds).
4. Trace one trust mechanism from spec → test → enforcement (e.g.
   VAL-5 motion gating, or ADR-21's self-verifying checker).
5. Then read the ENPIRE and ASPIRE papers with the §1 map in hand.

## 8. Primary sources

- [ENPIRE](https://arxiv.org/abs/2606.19980) — physical auto-research;
  [ASPIRE](https://arxiv.org/abs/2607.00272) — agentic skill
  libraries. The systems AISLE reproduces on open middleware (design
  doc §10 for the honest comparison).
- [DreamZero](https://arxiv.org/abs/2602.15922) — the WAM paradigm;
  [EgoScale](https://arxiv.org/abs/2602.16710) — the human-video
  scaling law; [DreamDojo](https://arxiv.org/abs/2602.06949) — the
  neural simulator.
- *Code as Policies* (Liang et al.) and *Voyager* (Wang et al.) — the
  code-as-policy and skill-library ancestors of `skills/`.
- dora-rs (`dora-rs.ai`) and Genesis
  (`genesis-world.readthedocs.io`) — the substrate.
- `references/Physical_AI_Unified_Report_v2.md` — the dated industry
  snapshot (unaudited synthesis; see its provenance and calibration
  notes).
