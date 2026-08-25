# Next phases — the model tier and the road to hardware

Status: **RATIFIED 2026-08-17**; progress ledger updated 2026-08-25.
The design document (Project_AISLE_Experiment_Design.md) is the frozen
pre-registration artifact and is never content-updated; THIS file is
the living plan reconciled against measured results.

## Progress ledger (2026-08-25)

| Item | State |
|---|---|
| 5.1 SmolVLA integration | DONE (#263/#277/#278/#300): typed node, ADR-38 preemption, async inference. Zero-shot structurally impossible (uninitialized base normalizers); SO-101 fine-tune pipeline validated end-to-end — first live policy 0/8, mechanism = CPU latency vs the staleness floor as designed. GPU-peer inference is the measured unlock. |
| 5.1b hybrid fallback | DONE (#293): decision-node arbitration; value contrast awaits a live policy. |
| 5.2 vlm-verifier | v1 DONE (#279/#292): offline judge + promotion-gated bench; 500M disqualified (0.2, 4/5 false-success). Open: the 2B+/label-rendered retry — also a Phase-6 entry criterion. |
| 5.3 world-model-env | NOT STARTED (M3's replay dataset exists). |
| M1 / M5 | Blocked on GPU (live policy). |
| H6 (design doc §6, Operation) | REGISTERED, NEVER RUN — machinery exists (swap/probe/H4 latency), no GPU needed. The strongest open experiment. |
| Owner decisions | 1 ratified; 3 (T4 inc-2) DONE (#295–#303: 3/3 + 1/3); 4 (parked skills) DONE (#304: registered 0.5). OPEN: 2 — GPU budget. |
| Since ratification | T2 breakthrough 0.375 (#299); accumulation differential equal-score/35%-cheaper (#306); standing lever = transit collisions (both tiers). |

## Why models now

Phases 2–3 established the substrate result with model-light pipelines
on purpose ("the loop must first work model-light so the agentic
contribution is cleanly isolated", §7.5). Three measured facts now
point at learned components as the highest-value next axis:

1. **T2/T3 are the standing wall.** No arm — wiped, library-backed,
   params-only, Claude, or Codex — solved them at session budgets. The
   classical perception/rearrangement stack is the binding constraint,
   and these are exactly the sub-problems VLA-class policies exist for.
2. **The verifier fidelity question is now sharp.** #248 showed policy
   and realistic verifier share a detector backbone at L2; a
   `vlm-verifier` (Cosmos-Reason class) is the independence-preserving
   judge — and the one that generalizes to T4's open-ended recovery.
3. **The trust-tier path is proven end-to-end** (ik-transfer-v2,
   §9.4): the governance machinery that would gate a learned motion
   policy has been exercised once on the class that matters.

## Phase 5 — the model-node tier (≈4 weeks, sim-only)

Three registry capability classes (§7.5), landed in dependency order,
each behind the SAME typed contract and frozen scorer as everything
else. No frozen code changes; new nodes are ordinary registry entries.

### 5.1 `vla-policy` nodes (weeks 1–2)

- Candidates, smallest-first: **SmolVLA** (fits the MacBook inference
  budget; the bring-up target), then **π0-class** and **GR00T N1.7**
  via a GPU host as dataflow peers (§7.5 placement split — the topic
  contract doesn't care where inference runs).
- Manifest shape: `provides: [manipulation_policy]`, consuming
  `rgb + joint_state + instruction`, emitting `joint_traj`;
  `safety_class: motion` — every command still traverses the budget
  guard, and H5's zero-wrong-medicine claim gets its hardest test.
- Integration order: (a) T1 A/B against the classical pipeline (M1
  baseline); (b) **hybrid fallback** — the VLA as the branch the state
  machine takes when the failure taxonomy says `never_grasped`; (c)
  the T2 attempt — label-conditioned instruction ("pick the box
  labeled cetirizine") against the tier both classical arms lost.
- Chunk preemption rule (tech report §11 table): specified BEFORE the
  first motion inference runs, like every safety rule here.

### 5.2 `vlm-verifier` node (week 2, parallel)

- Cosmos-Reason-class judge: "did the robot place amoxicillin in the
  tray? answer from these two views." Runs beside the detector+rules
  realistic verifier; report BOTH fidelities against the oracle, with
  `harness fidelity`'s backbone verdict (#250) distinguishing the
  independent judge from the correlated one.
- This is also the T4-increment-two enabler: an open-ended recovery
  dialogue needs a judge that reads scenes, not rules.

### 5.3 `world-model-env` node (weeks 3–4)

- The three-tier environment ladder (§7.5): neural sim for cheap
  candidate screening → Genesis for physics-verified iteration → (Phase
  6) hardware — same graph, one node swapped. First target: DreamDojo/
  Cosmos-Predict-class backbone consuming the same obs/cmd contract.
- Measurement M3: does neural-env candidate RANKING agree with Genesis
  ranking (the r=0.995 question) on the T1 graph population we already
  have from H1/H2 — a dataset that exists today.

### Phase-5 measurements (pre-registered before any run)

| Id | Question | Design |
|---|---|---|
| M1 | When does a learned policy add value? | classical vs VLA vs hybrid on T1 + T2, same seeds/budgets/scorer as A-series |
| M2 | Predictive planning? | direct policy vs world-model reranking on T3's rearrangement |
| M3 | Neural env for screening? | H1/H2 graph population re-ranked in neural env vs Genesis |
| M5 | **Does H5 survive learned motion?** | wrong-medicine under VLA-driven arms — the headline safety claim under its hardest condition |

And the deepest one (report §10.7): give a research agent the model
nodes in its registry and measure whether it REACHES for them where
classical failed — T2 is the natural arena, with the A3 result
(schema-as-subsidy) as the prior.

### Budgets and infrastructure asks

- **GPU host decision (owner):** SmolVLA runs locally; π0/GR00T and
  Cosmos-class need a CUDA peer. The contract already supports remote
  nodes; the ask is hardware/cloud budget, not code.
- Token/episode budgets: A-series scale (per-arm 0.4M/40 episodes) for
  M1/M2; M3 is replay-only (cheap). Model weights pinned by hash in
  manifests (the env_hash discipline extended to weights).
- Known-open infra that Phase 5 inherits (phase report ledger):
  sim-lockstep in the rollout path for fleet fidelity; the analyzer
  flag follow-ups; codex token-counter drift; eval-suite content
  pinning (the era-fragility lesson — model evalcards MUST pin weights
  + graph content, never paths).

## Phase 6 — hardware (unchanged scope, sharpened entry)

The §8.5 stretch as written — SO-101 or the bench-suite station as the
first target (the powder workstation remains the best-instrumented
candidate: the balance is a free continuous verifier). Entry criteria
now concrete: M1 hybrid ≥ classical on T1/T2 in sim; M5 wrong-medicine
0 sustained; the vlm-verifier fidelity number ≥ the detector verifier's
(the portable judge is the sim-to-real bridge). CONTRACT.md discipline
means the swap is a driver node.

## Owner decision points (in order of when they block)

1. **Ratify this phase plan** (it is PROPOSED; protocol ADRs follow
   per-measurement as with h1..h4/a3/a5).
2. **Model shortlist + GPU budget** for 5.1/5.3 (SmolVLA-only start
   needs neither; the full M1 contrast does).
3. **T4 increment two** (post-delivery recovery, ADR-32 §3): its VER
   amendment epoch pairs naturally with the vlm-verifier work.
4. **Retention of the parked skills** (t2-scan-pose n≥9 re-attempt is
   cheap once T2 stability work lands — the lockstep rollout path).
