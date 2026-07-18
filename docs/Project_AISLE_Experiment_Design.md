# Project AISLE: Agentic Auto-Research on an Open Dataflow Stack

**AISLE** — *Agentic In-Store Learning Environment* (formerly "Project Apothecary"; renamed v0.4 when the retail suite §11 outgrew the pharmacy-desk namesake).

**Experiment design — coding agents (Claude Code / Codex) as the brain, dora-rs as the execution layer, Genesis World as the physics loop**
*Draft v0.4, July 2026 — renamed Apothecary→AISLE; retail suite added (§11); §8 expanded into a full implementation guide for the student team; §9 resolved to decisions; model-orchestration tier (§7.5); ENPIRE/ASPIRE comparison (§10)*

---

## 0. One-paragraph pitch

ENPIRE proved that fleets of coding agents can run the full robotics research loop on real hardware — but it did so on a bespoke, closed harness. This experiment rebuilds that loop on **open, composable infrastructure**: the agent's action space is not "edit a monolithic training script" but **compose and evolve a typed dataflow** — generating dora-rs YAML from a capability registry (dora-hub manifests), authoring new nodes as skills, and iterating against automatic reset/verification in a Genesis World pharmacy scene. The claim under test: *a typed dataflow substrate makes agentic robotics faster, more auditable, and more reusable than script-level iteration* — and it's reproducible on a laptop.

---

## 1. Scenario and task definition

**Scene: the pharmacy desk.** A counter workspace with: a 3-tier shelf holding N medicine boxes (distinct sizes, colors, printed labels), a delivery zone (tray on the counter), 2–4 distractor objects (stapler, cup, card reader), and a fixed-base manipulator behind the counter. A "human" issues natural-language requests.

**Goal (given to the agent, verbatim):**
> "You operate a pharmacy assistant robot. When a request names a medicine, deliver exactly that item to the tray. You may compose dataflows from the capability registry, write new dora nodes, and run rollouts in the environment. You may not modify environment, reset, or verifier code — that is cheating. Maximize verified success within budget; a wrong medicine delivered is 10x worse than a failure to deliver."

The asymmetric penalty is deliberate: pharmacy makes **precision** (never the wrong med) a first-class metric, which is more interesting — and more deployment-realistic — than raw success rate.

**Task tiers (curriculum and transfer probes):**

| Tier | Task | New difficulty | Probes |
|---|---|---|---|
| T0 | Pick a known box at a fixed pose → tray | none (sanity) | env bring-up, graph launches |
| T1 | Pick the *named* medicine among 5, randomized poses | grounding + grasping | YAML composition (H1), iteration (H2) |
| T2 | Medicine identified by *label text only* (no color prior) | OCR / open-vocab perception | perception-skill authoring |
| T3 | Target box partially occluded behind another | re-arrangement subtask | skill composition, planning |
| T4 | Full request loop: confirm name back, deliver, verify placement, handle "that's the wrong one" | HRI + recovery | in-context retry (pass@k semantics) |

Beyond the desk curriculum, §11 defines the **retail competition suite (S1–S3)** — mobile, long-horizon scenarios that extend the same architecture to a store environment.

---

## 2. System architecture

```
┌─────────────────────────────────────────────────────────────┐
│  BRAIN: coding agent(s)  (Claude Code / Codex / Kimi Code)   │
│  harness: CLAUDE.md contract + tool APIs + git worktrees     │
│    registry.search() · dataflow.validate() · run.rollout()   │
│    traces.query() · env.reset() · skill.register()           │
└──────────────┬───────────────────────────────┬──────────────┘
               │ writes                        │ reads
               ▼                               ▼
┌────────────────────────────┐   ┌────────────────────────────┐
│  ARTIFACTS (git)           │   │  EVIDENCE                  │
│  dataflow.yaml (composed)  │   │  Arrow traces (dora record)│
│  skills/ (new dora nodes)  │   │  per-rollout video, reward │
│  manifests/ (capability)   │   │  idea-tree log (branch/node)│
│  evalcards/ (skill stats)  │   │  MRU/MTU/token dashboards  │
└──────────────┬─────────────┘   └────────────▲───────────────┘
               │ dora start                    │
               ▼                               │
┌─────────────────────────────────────────────┴───────────────┐
│  EXECUTION: dora-rs runtime                                  │
│  camera → detect → ground → pose → grasp-plan → arm-ctl      │
│  … plus mandatory system nodes:                              │
│  [env-bridge] [verifier] [reset] [recorder] [budget-guard]   │
└──────────────┬───────────────────────────────────────────────┘
               │ obs/cmd topics (Arrow)
               ▼
┌──────────────────────────────────────────────────────────────┐
│  WORLD: Genesis World scene (pharmacy_desk.py)               │
│  batched N-env option = virtual robot fleet                  │
│  stage 2: Isaac Lab photoreal validation (optional)          │
└──────────────────────────────────────────────────────────────┘
```

The four ENPIRE modules map cleanly:

- **EN (Environment)** → `dora-genesis` bridge node + `verifier` node + `reset` node. Frozen code, read-only mount, hash-checked at launch.
- **PI (Policy Improvement)** → the agent editing dataflow YAML, node params, or node code.
- **R (Rollout)** → `run.rollout(dataflow, n_episodes, seeds, budget)`; batched Genesis envs give fleet-scale rollouts on one GPU.
- **E (Evolution)** → git worktree per agent branch; idea-tree logging; cross-agent summaries; `skill.register()` distills wins.

---

## 3. The capability registry (the load-bearing new artifact)

The experiment's central bet is that **typed capability manifests turn graph composition into something an LLM can do reliably**. Each dora-hub node gets a `capability.yaml`:

```yaml
# manifests/grasp-planner-ik.yaml
id: grasp-planner-ik
kind: node                    # node | subgraph (composed skill)
provides: [grasp_planning]
requires: [object_pose, robot_urdf]
inputs:
  object_pose:  {schema: "pose6d_f32", rate_hz: "<=30"}
  joint_state:  {schema: "jointstate_f32[7]"}
outputs:
  joint_traj:   {schema: "traj_point_f32[7]", latency_class: "soft_100ms"}
params:
  approach_offset_m: {type: float, default: 0.10, range: [0.02, 0.25]}
embodiment: {arm: ["franka", "piper", "so101"], gripper: parallel}
safety_class: motion          # perception | decision | motion
eval: {suite: "grasp_bench_v0", pass_rate: 0.87, last_run: 2026-07-01}
origin: hub                   # hub | agent-authored
```

Rules the harness enforces:
1. **Schema-checked composition.** `dataflow.validate()` type-checks every edge (Arrow schema match, rate compatibility) before anything runs. Invalid graphs are rejected with machine-readable errors — the agent's compile loop. This is where composition quality comes from; without typed IO, agents wire nonsense.
2. **Safety classes.** Only `safety_class: motion` nodes may command the arm; the `budget-guard` node interposes on the command topic (joint limits, velocity caps, workspace box, episode timeout). Agent-authored nodes default to the most restrictive class until an evalcard exists.
3. **Skills are subgraphs with manifests.** A distilled skill = YAML fragment + node code + manifest + evalcard (success stats per task tier, per embodiment). `kind: subgraph` lets a skill nest as a single node in later graphs — the ASPIRE library, expressed natively in dataflow.

Initial registry (~12 manifests to hand-write in Phase 1, mapped to actual dora-hub nodes where they exist): camera source, object detector (open-vocab), OCR/label reader, pose estimator, grasp planner, IK/trajectory node, arm driver (sim), gripper driver (sim), speech/text intake, task-state machine, verifier, reset. Where a hub node exists, the manifest wraps it; gaps are seeded as **deliberate holes for the agent to fill** (e.g., ship no occlusion-rearrangement skill — T3 forces authorship).

---

## 4. Environment module in detail

### 4.1 `dora-genesis` bridge node
A single Python node embedding Genesis:
- **Outputs:** `rgb` (per camera: overhead + wrist), `depth`, `joint_state`, `contact_forces`, `sim_time` — plus a privileged `oracle_state` topic (all object poses/IDs) published **only** to the verifier, never routed to policy nodes (validator enforces this edge restriction).
- **Inputs:** `joint_cmd` (position targets) or `traj_point`, `gripper_cmd`.
- **Config:** scene seed, physics dt, camera intrinsics, `n_envs` for batched mode, domain-randomization toggles (box poses, lighting, textures, friction, camera jitter).
- Runs headless; Nyx renderer optional for photoreal passes.

### 4.2 Auto-verification (frozen)
Two verifiers, run together:
- **Oracle verifier** (ground truth): correct object ID inside tray volume, upright, robot home → binary reward + failure taxonomy (`wrong_object`, `dropped`, `unreachable`, `timeout`, `collision`, `never_grasped`).
- **Realistic verifier** (portable): detector + segmentation on rendered cameras, per-camera verdicts fused — the same code that would run on real hardware. Report **verifier fidelity** (agreement with oracle) as a first-class result; it's the number that says whether this loop ports to a physical desk.

### 4.3 Auto-reset (frozen, two modes)
- `teleport`: state reset via sim API (fast inner loop).
- `behavioral`: the robot must physically return the box to a randomized shelf pose and verify readiness — parity with the real-world path, and itself a skill the loop must maintain. Ablation A6 measures what teleporting hides.

---

## 5. The agent harness

**Contract (CLAUDE.md):** goal text (§1), tool API docs, registry location, budget (tokens, rollouts, wall-clock), the no-cheating rule, and the reporting format (every idea gets a one-line hypothesis + expected effect before running — this is what makes the idea-tree legible afterward).

**Tool APIs (thin CLI/MCP wrappers):**
- `registry.search(query|capability)` → manifests
- `dataflow.validate(yaml)` → ok | typed errors
- `run.rollout(yaml, n, seeds, tier, reset_mode)` → success vector, failure taxonomy, trace IDs, video paths
- `traces.query(run, node, topic, t0, t1)` → Arrow slices (the replay/inspect loop; this is dora-record data)
- `env.info()` / `env.reset(seed, mode)`
- `skill.register(path, manifest)` → runs the skill's eval suite, writes evalcard
- `report.log(idea, parent, expected, observed)` → idea-tree

**Multi-agent mode:** N agents, one git worktree each, shared read access to peers' branch summaries (ENPIRE's cross-pollination), a scheduler that multiplexes rollout requests onto the batched sim. Because Genesis envs are cheap, the "robot fleet" is `n_envs` — we can study fleet-scaling laws (MRU/MTU) for a few dollars of GPU time instead of eight physical arms.

---

## 6. Hypotheses, metrics, ablations

**Hypotheses**
- **H1 (Composition):** Given the goal + registry, a frontier coding agent composes a *valid, launching* dataflow for T1 zero-shot ≥80% of attempts, and reaches a working (>0% success) graph within 3 validate-fix cycles.
- **H2 (Iteration):** With the EN loop, agents raise T1/T2 success from baseline to ≥90% pass@1 (≥99% pass@8 in-context retries, ENPIRE semantics) within a fixed budget.
- **H3 (Accumulation):** A persistent skill library cuts time-to-success on T3/T4 by ≥2x vs. a memory-wiped agent (the ASPIRE effect, measured).
- **H4 (Substrate):** Dataflow-level iteration (edit YAML/params/nodes, hot-swap, replay traces) beats an equal-budget monolithic-script control condition on time-to-success and on audit legibility (every change is a diff on a typed graph).
- **H5 (Safety):** The typed/safety-class harness holds wrong-medicine deliveries at 0 across all runs even while agents freely author motion code.

**Primary metrics:** success pass@1 / pass@8 per tier; **wrong-medicine rate** (target: 0); time-to-first-success and time-to-90% (wall-clock); tokens-to-success; graph-validity rate; verifier fidelity; skill-reuse count; MRU analogue (sim-step utilization vs. agent think-time) and MTU.

**Ablations**
- A1: agent-composed graph vs. hand-written expert graph (composition tax or gain?)
- A2: skill library on/off across tiers (H3)
- A3: params-only vs. params+code authorship
- A4: Claude Code vs. Codex vs. Kimi Code (AutoEnvBench-style agent comparison)
- A5: 1 vs. 4 vs. 8 agents on batched envs (fleet scaling; token super-linearity check)
- A6: teleport vs. behavioral reset
- A7: oracle vs. realistic verifier driving the loop (does the portable verifier's noise break learning?)

---

## 7. Simulator strategy: Genesis primary, Isaac Lab validation

**Genesis World as the inner loop.** Rationale: pip-installable pure Python (agents can read and reason about the whole stack), Apache 2.0, cross-platform including Apple Metal (Mac dev) and CUDA (Linux workstation), URDF/MJCF/USD ingestion, batched + heterogeneous parallel envs (the virtual fleet), built-in depth/tactile/contact sensors and domain randomization, headless-friendly. The agent can *construct the scene as code* — which means scene variation (T3 occlusion layouts, shelf randomization) is itself in the agent's action space if we choose to allow it later.

**Isaac Sim/Lab as stage-2 validation (optional).** Photoreal RTX rendering for perception stress-tests, Newton-engine contact fidelity cross-check on the grasp, and stack-alignment if this graduates toward GR00T/Cosmos integration. Not in the inner loop: heavyweight, Linux+RTX-bound, slower for agent-driven iteration. A third cross-check for contact physics: replicate the grasp micro-benchmark in MuJoCo (Genesis cites it as its rigid-body reference) to catch simulator-specific exploits.

**Perception ladder** (avoids conflating perception difficulty with loop capability): L0 oracle poses → L1 ground-truth segmentation, estimated pose → L2 full pipeline (detector + OCR on rendered pixels, domain-randomized). Tiers T0–T1 start at L1; T2+ requires L2. Report results per rung.

**Known sim risks:** thin-box grasping is contact-parameter sensitive (tune friction/SAP; verify with the MuJoCo cross-check); rendered-label OCR legibility needs a font/texture pass; agents exploiting physics bugs is a *finding*, not just a nuisance — log and report exploit discoveries separately.

### 7.5 Model orchestration: dora as the glue layer for models

dora's essential role here is **multi-model orchestration** — the dataflow composes classical nodes *and* model-inference nodes behind the same typed topic contract. Three model-node classes enter the registry as first-class capabilities:

- **`vla-policy` nodes** (GR00T N1.7, π0-class, SmolVLA): `provides: [manipulation_policy]`, consuming `rgb + joint_state + instruction`, emitting `joint_traj`. This makes "engineered pipeline vs. learned policy vs. hybrid" an *agent decision* recorded in the idea tree — the agent can A/B its own composed grasp pipeline against a VLA on the same verifier, or use the VLA as a fallback branch when the pipeline's failure taxonomy says `never_grasped`.
- **`vlm-verifier` nodes** (Cosmos-Reason class): an alternative realistic verifier — "did the robot place amoxicillin in the tray? answer from these two camera views" — sitting alongside the detector+rules verifier. Report both verifiers' fidelity against the oracle; a VLM verifier is the one that generalizes to T4's open-ended recovery dialogue.
- **`world-model-env` nodes** (DreamDojo-class, Cosmos-Predict backbones): the deepest unification — because the environment is *just another node* behind the obs/cmd topic contract, the same policy dataflow can target `dora-genesis` (physics sim), `world-model-env` (neural sim), or a hardware driver, by swapping one node. This yields a **three-tier environment ladder** — neural sim for cheap candidate screening, Genesis for physics-verified iteration, real hardware for grounding — with graph identity preserved across all three. Tier-agreement (does neural-sim ranking match Genesis ranking match reality, DreamDojo's r=0.995 question) becomes measurable inside one runtime.

Inference placement follows the standard split: heavy model nodes run on the GPU host as dataflow peers; the topic contract doesn't care. For v0, model nodes are optional registry entries (Phase 3+); the loop must first work model-light so the agentic contribution is cleanly isolated.

---

## 8. Phased implementation guide

This section is written for an implementation team of graduate students who have *not* built robotics or agent systems before. Each phase specifies: learning objectives (what the phase teaches you), the work breakdown with code, a definition of done (DoD), and the pitfalls we already know about. Code snippets are reference-grade: they show the intended shape against current dora-rs and Genesis APIs, but always check the live docs (`dora-rs.ai`, `genesis-world.readthedocs.io`) — APIs move, and part of the training is learning to reconcile a design doc with reality.

Timelines assume a 4-person team, roughly doubled from the expert estimates: Phase 0 ≈ 2 weeks, Phase 1 ≈ 3 weeks, Phase 2 ≈ 4 weeks, Phase 3 ≈ 4 weeks.

### 8.0 Team structure, repo, and ground rules

**Workstreams (one owner each, everyone reviews everything):**
- **W1 World** — Genesis scene, bridge node, verifier, reset (Phase 0 critical path)
- **W2 Runtime** — manifests, validator, dataflow tooling, budget-guard, dynamic-graph ops
- **W3 Agent** — harness, CLAUDE.md, tool CLIs, idea-tree, evaluation protocol
- **W4 Metrics & Infra** — recorder, trace queries, dashboards, CI, token accounting

**Repository layout (monorepo):**

```
aisle/
├── CLAUDE.md                  # the agent contract (Phase 1) — also onboarding doc for humans
├── env/                       # FROZEN after Phase 0 sign-off (read-only mount for agents)
│   ├── dora_genesis/          #   bridge node package
│   ├── scenes/pharmacy.py     #   scene builder
│   ├── verifier/              #   oracle + realistic verifier nodes
│   └── reset/                 #   teleport + behavioral reset node
├── registry/
│   ├── schema/capability.schema.json
│   └── manifests/*.yaml
├── graphs/                    # dataflow YAMLs (expert baselines + agent-composed)
├── skills/                    # agent-authored nodes & subgraphs + evalcards
├── harness/                   # tool CLIs: validate, rollout, traces, registry, report
├── runs/                      # gitignored: traces, videos, results (content-addressed)
└── analysis/                  # notebooks, plots
```

**Ground rules (these are the experiment's integrity):**
1. Everything under `env/` is hash-manifested at Phase 0 sign-off; the rollout runner refuses to start if hashes differ (this is the ENPIRE "no cheating" rule, enforced, not requested).
2. Every tool the agent uses is a CLI that prints JSON to stdout. Claude Code's native tool is the shell — if your tool needs a bespoke integration, you built the wrong thing.
3. Every rollout is reproducible from `(graph hash, env hash, seed list)`. If a student can't re-run a result from three days ago, that's a P0 bug.
4. Humans use the same tools as agents. If `harness/rollout.py` is annoying for you, it's confusing for Claude.

**Pre-reading (first two days, before writing code):** the ENPIRE project page (research.nvidia.com/labs/gear/enpire) and paper; ASPIRE release notes; *Code as Policies* (Liang et al.); *Voyager* (Wang et al. — the skill-library ancestor); dora-rs getting-started + the YAML dataflow spec; Genesis tutorials `control_your_robot.py` and `franka_cube.py`. Each student presents one of these to the group in 15 minutes — you learn a system by explaining it.

---

### 8.1 Phase 0 — World bring-up (≈2 weeks)

**Learning objectives:** what a dataflow runtime is and why robotics uses one; how a physics simulator is driven step-by-step; why verification and reset — not the pick itself — are the hard engineering.

#### 8.1.1 The pharmacy scene (`env/scenes/pharmacy.py`)

A scene builder function, parameterized by seed and embodiment — *not* a script. Everything downstream (reset, batching, domain randomization) depends on the scene being reconstructible from a config.

```python
import genesis as gs
import numpy as np

MEDS = [  # name, size (m), rgba — labels rendered as textures in Phase 2
    ("amoxicillin", (0.10, 0.035, 0.055), (0.9, 0.3, 0.3, 1)),
    ("ibuprofen",   (0.09, 0.030, 0.050), (0.3, 0.5, 0.9, 1)),
    ("cetirizine",  (0.11, 0.040, 0.060), (0.3, 0.8, 0.4, 1)),
    ("omeprazole",  (0.08, 0.030, 0.045), (0.9, 0.7, 0.2, 1)),
    ("metformin",   (0.10, 0.035, 0.055), (0.7, 0.4, 0.8, 1)),
]

def build_scene(seed: int, embodiment: str = "franka", n_envs: int = 1,
                headless: bool = True):
    rng = np.random.default_rng(seed)
    scene = gs.Scene(
        sim_options=gs.options.SimOptions(dt=0.01),
        show_viewer=not headless,
    )
    scene.add_entity(gs.morphs.Plane())
    counter = scene.add_entity(gs.morphs.Box(size=(1.2, 0.6, 0.04),
                                             pos=(0.55, 0.0, 0.40), fixed=True))
    tray = scene.add_entity(gs.morphs.Box(size=(0.25, 0.18, 0.02),
                                          pos=(0.45, -0.35, 0.43), fixed=True))
    shelf_z = [0.55, 0.70, 0.85]
    boxes = {}
    for i, (name, size, rgba) in enumerate(MEDS):
        x = 0.62 + rng.uniform(-0.03, 0.03)
        y = -0.30 + i * 0.15 + rng.uniform(-0.02, 0.02)
        z = rng.choice(shelf_z) + size[2] / 2
        boxes[name] = scene.add_entity(
            gs.morphs.Box(size=size, pos=(x, y, z)),
            surface=gs.surfaces.Default(color=rgba),
        )
    if embodiment == "franka":
        robot = scene.add_entity(
            gs.morphs.MJCF(file="xml/franka_emika_panda/panda.xml"),
            # base placed behind the counter
        )
    elif embodiment == "so101":
        robot = scene.add_entity(gs.morphs.URDF(file="assets/so101/so101.urdf"))
    cams = {
        "overhead": scene.add_camera(res=(640, 480), pos=(0.6, 0.0, 1.4),
                                     lookat=(0.6, 0.0, 0.5), fov=55),
        "wrist":    scene.add_camera(res=(320, 240), fov=70),  # attach to EE link
    }
    scene.build(n_envs=n_envs)
    return scene, robot, boxes, tray, cams
```

*Pitfalls:* box masses and friction defaults will make thin boxes skate or wedge — expect a week of contact tuning; that *is* the learning. Keep every tuned parameter in a `physics.toml`, never inline. Verify determinism early: same seed → same trajectories (needed for reproducibility rule 3).

#### 8.1.2 The bridge node (`env/dora_genesis/main.py`)

One dora node owns the simulator and speaks Arrow on topics. This file defines the **driver topic contract** — write it as a table in `env/CONTRACT.md` first (topic, schema, rate, units, frame), then implement. The contract, not this code, is what Phase 4 hardware must honor.

```python
# Topic contract v0 (env/CONTRACT.md):
#   OUT rgb_overhead   : UInt8, shape [H*W*3], meta {h, w, enc:"rgb8", env_id}
#   OUT rgb_wrist      : same
#   OUT joint_state    : Float32 [n_dof]  (rad), meta {names: [...], env_id}
#   OUT oracle_state   : Float32 [n_obj*7] (xyz+quat per box, fixed order)
#   IN  joint_cmd      : Float32 [n_dof]  position targets (rad)
#   IN  gripper_cmd    : Float32 [1]      0.0=open … 1.0=closed
#   IN  reset          : UInt32  [1]      seed
import numpy as np, pyarrow as pa
from dora import Node
from aisle.scenes.pharmacy import build_scene

node = Node()
scene = robot = boxes = cams = None

def do_reset(seed):
    global scene, robot, boxes, tray, cams
    scene, robot, boxes, tray, cams = build_scene(seed=int(seed))

do_reset(seed=0)
for event in node:
    if event["type"] != "INPUT":
        continue
    if event["id"] == "reset":
        do_reset(event["value"][0].as_py())
        node.send_output("reset_done", pa.array([1], type=pa.uint32()))
    elif event["id"] == "joint_cmd":
        q = np.asarray(event["value"]).astype(np.float32)
        robot.control_dofs_position(q)          # PD position control
    elif event["id"] == "gripper_cmd":
        ...                                     # map to finger joints
    elif event["id"] == "tick":                 # dora timer drives sim time
        scene.step()
        rgb, _, seg, _ = cams["overhead"].render(rgb=True, segmentation=True)
        node.send_output("rgb_overhead", pa.array(rgb.ravel()),
                         metadata={"h": 480, "w": 640, "enc": "rgb8"})
        node.send_output("joint_state",
                         pa.array(robot.get_dofs_position().cpu().numpy(),
                                  type=pa.float32()))
        poses = np.concatenate([np.concatenate([b.get_pos().cpu().numpy(),
                                                b.get_quat().cpu().numpy()])
                                for b in boxes.values()]).astype(np.float32)
        node.send_output("oracle_state", pa.array(poses))
```

*Pitfalls:* don't render every camera every tick — rate-limit rendering to camera Hz (the metadata carries `sim_time` so consumers can align). Decide now that all angles are radians and all frames are the robot base frame; unit bugs cost weeks. `oracle_state` exists on this node but the **validator forbids routing it to anything except the verifier** (Phase 1 enforces; Phase 0 discipline).

#### 8.1.3 Oracle verifier and reset nodes

```python
# env/verifier/oracle.py — frozen after sign-off
TRAY_AABB = ((0.325, -0.44, 0.42), (0.575, -0.26, 0.55))
FAIL = ["wrong_object", "dropped", "timeout", "never_grasped", "collision"]

def judge(oracle_state, target_idx, t, episode_cfg):
    pose = oracle_state.reshape(-1, 7)
    in_tray = lambda p: all(lo <= c <= hi for c, lo, hi in
                            zip(p[:3], TRAY_AABB[0], TRAY_AABB[1]))
    for i, p in enumerate(pose):
        if in_tray(p):
            return ("success", None) if i == target_idx else ("fail", "wrong_object")
    if t > episode_cfg.timeout_s:
        return ("fail", "timeout")
    return ("running", None)
```

The verifier is a dora node subscribing to `oracle_state` + the episode's `target` and publishing `episode_result` (status, failure class, `t_end`). Reset node v0 = republish `reset` with a fresh seed on `episode_result`. Behavioral reset waits for Phase 2.

#### 8.1.4 The expert baseline graph (`graphs/expert_t0.yaml`)

Hand-write the T0 pipeline before any agent touches anything — it is your integration test, your A1 baseline, and the team's dora tutorial:

```yaml
nodes:
  - id: sim
    path: dora-genesis
    inputs:
      tick: dora/timer/millis/10
      joint_cmd: controller/joint_cmd
      gripper_cmd: controller/gripper_cmd
      reset: reset/seed
    outputs: [rgb_overhead, joint_state, oracle_state, reset_done]

  - id: perception          # Phase 0: oracle-pose passthrough (ladder rung L0)
    path: nodes/oracle_pose.py
    inputs: { oracle_state: sim/oracle_state, target: task/target }
    outputs: [object_pose]

  - id: grasp_planner
    path: nodes/topdown_grasp.py     # pregrasp→descend→close→lift→tray, IK via scene
    inputs: { object_pose: perception/object_pose, joint_state: sim/joint_state }
    outputs: [joint_cmd, gripper_cmd]
    # NOTE: id 'controller' in edges above — keep ids consistent; the validator
    # you build in Phase 1 will catch this class of error automatically.

  - id: verifier
    path: env/verifier/oracle.py
    inputs: { oracle_state: sim/oracle_state, target: task/target }
    outputs: [episode_result]
```

**DoD (Phase 0):** `dora start graphs/expert_t0.yaml` completes ≥95/100 seeded episodes; a `runs/<id>/` directory contains per-episode result JSON + overhead video; same seed reproduces the same result; `env/` hash manifest generated; CONTRACT.md reviewed and signed by the whole team.

---

### 8.2 Phase 1 — Registry, validator, harness (≈3 weeks)

**Learning objectives:** schema-first engineering; why typed composition is what makes LLM tool-use reliable; how a coding agent actually consumes an environment (hint: through boring CLIs).

#### 8.2.1 Capability manifests

Write `registry/schema/capability.schema.json` (JSON Schema for the YAML in §3) and ~12 manifests. Non-obvious fields that earn their keep: `latency_class` (the validator warns when a `soft_100ms` output feeds a `hard_10ms` consumer), `embodiment`, `safety_class`, and `eval` (empty until an evalcard exists — the validator refuses `safety_class: motion` nodes with no evalcard unless `--allow-unproven` is set, which the harness never sets for agents).

#### 8.2.2 The validator (`harness/validate.py`)

The single most leveraged component in the project. It loads a dataflow YAML + all manifests and checks, edge by edge:

```python
CHECKS = [
    check_node_ids_unique,
    check_every_input_has_producer,        # catches the 'controller' typo above
    check_schema_match,                    # Arrow type + shape compatibility
    check_rate_compat,                     # producer rate vs consumer rate_hz bound
    check_oracle_isolation,                # oracle_state may only reach verifier/*
    check_motion_gate,                     # any joint_cmd path must pass budget_guard
    check_embodiment_consistent,           # all nodes support the graph's embodiment
    check_safety_class_evalcards,
]

def validate(graph_path) -> dict:
    graph, manifests = load(graph_path), load_manifests()
    errors, warnings = [], []
    for check in CHECKS:
        check(graph, manifests, errors, warnings)
    return {"ok": not errors, "errors": errors, "warnings": warnings}
```

Error messages are the agent's learning signal — make them machine-actionable:

```json
{"ok": false, "errors": [{
  "code": "SCHEMA_MISMATCH",
  "edge": "perception/object_pose -> grasp_planner/object_pose",
  "produced": "pose6d_f32", "expected": "pose7d_f32",
  "hint": "insert an adapter node or use pose-convert from the registry"
}]}
```

*Assignment framing:* the student who owns this writes 20 deliberately broken graphs as its test suite. Every failure class an agent later hits should already be a test case; when the agent finds one you didn't, add it — that list is a paper table.

#### 8.2.3 Harness CLIs and the agent contract

Every tool: argparse in, JSON out, exit code = ok. `harness/rollout.py` is the workhorse:

```
usage: rollout.py --graph graphs/agent_x.yaml --tier T1 --episodes 20 \
                  --seeds 0..19 --reset teleport --budget-episodes-left auto
→ {"run_id": "r_2026_...", "pass1": 0.35,
   "failures": {"never_grasped": 9, "wrong_object": 0, "timeout": 4},
   "traces": "runs/r_.../", "videos": ["runs/r_.../ep03.mp4", ...],
   "budget": {"episodes_left": 412, "tokens_logged": true}}
```

It: verifies `env/` hashes, calls `validate` (refuses invalid graphs), launches the graph per episode batch, subscribes to `episode_result`, records via dora-record, and writes the run manifest. `harness/traces.py query --run r_... --node grasp_planner --topic joint_cmd --episode 3` returns Arrow slices as JSON/NPZ for the agent's failure analysis.

**CLAUDE.md** (the contract) contains, in order: the goal text from §1; the rules (no editing `env/`, every idea logged *before* running via `harness/report.py log --idea "..." --parent I12 --expect "+10pp"`); tool usage examples (copy-paste-runnable — agents and students both learn from examples, not descriptions); the failure-taxonomy glossary; and budget semantics. Keep it under 300 lines; every line an agent must scroll past costs tokens on every turn.

#### 8.2.4 The H1 experiment

Protocol: fresh agent session, CLAUDE.md + registry access, task T1, N=20 attempts (fresh session each), record per attempt: (a) graph valid on first `validate`? (b) number of validate-fix cycles to launch; (c) pass@1 of the launched graph. Run identical protocol for Claude Code and Codex.

**DoD (Phase 1):** validator ships with ≥20-case test suite; 12 manifests merged; H1 table produced; composition-failure taxonomy written up; one full agent transcript annotated by the team (read what the agent actually did — this is the single best training exercise in the project).

---

### 8.3 Phase 2 — The full autoresearch loop (≈4 weeks)

**Learning objectives:** reward/verifier engineering; why agents exploit anything unspecified; reading agent research logs like an advisor reads a student's lab notebook.

Work items, each with an owner:

1. **Realistic verifier** (W1): detector (open-vocab, e.g., OWLv2/YOLO-World class) + SAM-class segmentation on rendered views, per-camera verdicts fused with AND — port of the ENPIRE zip-tie recipe. Report per-run **verifier fidelity** = agreement with oracle on identical episodes. The perception ladder config (`perception: L0|L1|L2`) switches which pose source the graph may use; L2 unlocks T2.
2. **Behavioral reset** (W1): the reset node commands the robot to return the box to a sampled shelf pose and *verifies* the reset with the realistic verifier before signaling ready. This is a manipulation task of its own; budget real time for it (ENPIRE: "reset is often easier than the task" — often, not always).
3. **Budget-guard node** (W2, frozen into `env/` after review): interposes on every `joint_cmd`/`gripper_cmd` edge (the validator rewires graphs so this is structural):

```python
LIMITS = load("env/limits.toml")   # per-joint pos/vel, EE workspace AABB, ep timeout
for event in node:
    if event["id"] == "joint_cmd":
        q = np.asarray(event["value"])
        if not within_limits(q, last_q, LIMITS):
            node.send_output("violation", encode(q, reason))
            q = clamp(q, last_q, LIMITS)
        node.send_output("joint_cmd_safe", pa.array(q)); last_q = q
```

4. **Idea tree** (W3): `report.py` appends JSONL `{idea_id, parent, branch, hypothesis, expected, runs: [...], observed, verdict}`; W4 renders it as the ENPIRE-style tree + success-over-wallclock plot. Enforcement: `rollout.py` refuses to run if the current git branch has no open idea entry.
5. **Hot-swap ops** (W2): wrap dora's dynamic-node API as `harness/swap.py --dataflow <id> --replace grasp_planner --with skills/grasp_v2` and `probe.py --attach --topic sim/joint_state --for 30s`. Instrument **iteration latency** (timestamp idea-log → timestamp first episode under the change) for both relaunch and hot-swap paths — this is the H4 headline plot.
6. **The campaign** (W3): single agent, T1 then T2, 5M token budget, seeds fixed, everything logged. Then ablations A1 (expert graph baseline), A3 (params-only flag in CLAUDE.md), A7 (loop driven by realistic verifier only, oracle held out for scoring).

*Pitfalls we can predict:* the agent will find a verifier loophole (e.g., knocking the right box into the tray counts) — decide *in advance* whether toppled-but-correct is success (write it into the verifier spec, don't improvise); the agent will burn tokens re-reading long logs — give `traces.py` a `--summarize` mode early; students will "help" the agent between sessions — the protocol forbids mid-campaign human hints except through CLAUDE.md diffs, which are versioned and reported.

**DoD (Phase 2):** pass@1/pass@8-over-wallclock curves for T1/T2; verifier-fidelity number; iteration-latency comparison; A1/A3/A7 tables; zero budget-guard *unclamped* violations; one written post-mortem per student on "the strangest thing the agent did."

---

### 8.4 Phase 3 — Skills, fleet, cross-embodiment (≈4 weeks)

**Learning objectives:** what makes a skill reusable (contract + evidence, not code); multi-agent coordination economics; reading scaling plots critically.

1. **Skill registration** (W2+W3): `harness/skill.py register skills/rearrange_occluder/` — validates the manifest, runs the skill's eval suite (a mini-rollout config shipped *with* the skill), writes the evalcard, opens a PR (governance: human merge). Subgraph skills are dora subgraphs; verify trace attribution shows `skill:<name>/<ver>` spans.
2. **The ASPIRE ablation (H3)**: same agent, T1→T2→T3→T4 sequentially, twice: library persisted vs. wiped between tiers. Metric: time/tokens-to-success per tier. T3 requires authoring the rearrangement skill; T4 requires a task-state-machine node with confirm/retry dialogue (scripted human-request generator so runs are reproducible).
3. **Fleet mode** (W4): N agents = N git worktrees, each with its own idea-tree branch; a 100-line scheduler multiplexes `rollout.py` requests onto a shared batched-sim server (`n_envs=16` Genesis build; the bridge node gains `env_id` routing). Peer visibility = read access to peers' idea-tree JSONL, refreshed between turns. Run 1/4/8 agents on T1-from-scratch; produce MRU-analogue (sim-step utilization vs. agent think time), MTU, tokens-to-success — plotted against ENPIRE's Figure 6 for direct comparison.
4. **Cross-embodiment** (W1): SO-101 profile in heterogeneous envs; re-run the winning graphs with only the driver/embodiment axis changed; document exactly which nodes needed variants (expect: grasp params yes, perception no — but *measure*).

**DoD (Phase 3):** H3 plot, fleet-scaling plots, agent-comparison table (A4), cross-embodiment table, skill library with ≥5 evalcarded skills, all agent PRs reviewed with written review notes (the notes are governance-paper data).

---

### 8.5 Phase 4 — Stretch (hardware TBD)

Unchanged in scope: (a) sim-to-real on whatever arm materializes — the CONTRACT.md discipline from Phase 0 is what makes this a driver-node swap; budget two weeks for camera calibration and gripper reality regardless; (b) optional Isaac Lab photoreal pass reusing the L2 perception stack; (c) release: `dora-autoresearch` harness, schema, scene, manifests, and the annotated agent transcripts as teaching material — positioned as the first reproducible, laptop-scale ENPIRE-class benchmark on open middleware.

### 8.6 How this trains the team (say it out loud at kickoff)

Phase 0 teaches simulation and dataflow fundamentals; Phase 1 teaches schema-first systems design and that agent reliability is an *interface* property; Phase 2 teaches experimental method — verifier rigor, pre-registered hypotheses (the idea log is exactly that), and adversarial thinking about your own reward functions; Phase 3 teaches research economics — when parallelism pays, what coordination costs, what evidence a claim needs. A student who completes all four can build agentic-control infrastructure anywhere in the industry; that is the point.

---

## 9. Resolved decisions (v0.2)

1. **Dynamic graphs: use them from Phase 2.** dora supports dynamic graphs/nodes, so the Evolution loop is not compose-then-relaunch: agents can attach/detach nodes on a live dataflow. Two immediate uses: (a) **hot-swap iteration** — replace a perception or planning node mid-run without killing the episode stream, directly attacking the ENPIRE idle-robot problem (robots keep executing while the agent revises one stage); (b) **live probes** — the agent attaches a temporary inspector node to any topic to diagnose a failure in situ, then detaches it, instead of re-running with added logging. Harness rule: dynamic attachment of `safety_class: motion` nodes still passes through `dataflow.validate()` + budget-guard before the runtime accepts the edge. Add a metric: **iteration latency** (idea → running change), compared between relaunch and hot-swap modes — this quantifies the substrate claim (H4) sharply.

2. **Skills are native subgraphs.** dora supports subgraphs, so `kind: subgraph` skills nest as single named nodes with no flattening — reuse identity is preserved in traces (a trace query can attribute a failure to `skill:rearrange-occluder/v3` as a unit). The skill library is therefore literally a directory of subgraph YAMLs + node code + manifests + evalcards, versioned in git and registrable to the hub.

3. **Model nodes are first-class capabilities (§7.5).** dora's role is glue for multiple models — VLA policies, VLM verifiers, and world-model environments enter the registry behind the same typed contract, making model-vs-pipeline-vs-hybrid an agent-observable decision and enabling the three-tier environment ladder (neural sim / physics sim / real) with one swapped node.

4. **Governance: human-in-the-loop now, trust tiers later.** Phase 1–3: every `origin: agent-authored` skill requires human review before hub registration (a PR — the agent opens it, you merge it; Git remains the coordination substrate exactly as in ENPIRE). Post-experiment roadmap: signed evalcards, trust tiers (`sandbox → reviewed → certified`), automatic promotion criteria (eval suite pass + N verified deployments), and per-tier safety-class ceilings — i.e., an uncertified skill can never hold `safety_class: motion` on real hardware regardless of its sim record. This governance layer is itself a contribution: nobody has specified skill-library trust for agent-authored robot code yet.

5. **Budget:** 5M tokens/agent for Phase 2, instrumented, then revisit from data (per ENPIRE's finding that token-to-success grows super-linearly with fleet size).

---

## 10. Positioning: AISLE vs. ENPIRE vs. ASPIRE

For team discussion. Short version: ENPIRE proved the *loop* on real hardware; ASPIRE proved the *skill library*; AISLE rebuilds both on an open, typed dataflow substrate and asks whether the substrate itself is the missing infrastructure. We are not competing with either result — we are proposing the runtime they arguably should run on.

### 10.1 What each system is

| | **ENPIRE** (NVIDIA GEAR + CMU + Berkeley, Jun 2026) | **ASPIRE** (NVIDIA GEAR, Jul 2026) | **AISLE** (this proposal) |
|---|---|---|---|
| Core claim | Coding agents can run the full research loop on real robots (physical auto-research) | Agents can debug robot programs and distill fixes into a compounding skill library | A typed dataflow substrate makes both loops faster, safer, auditable, and reusable |
| Agent action space | Edit monolithic training scripts + infra code | Edit code-as-policy programs; register skills | Compose typed dataflow YAML; hot-swap live nodes; author nodes/subgraph skills |
| Execution substrate | Bespoke harness (env.py + tool APIs), per-task | Code-as-Policy runtime | dora-rs runtime (open, cross-language, dynamic graphs, subgraphs) |
| Environment | 8 real dual-arm robots; hand-engineered reset/verify per task | Sim + real | Genesis batched sim (virtual fleet) → three-tier ladder (neural sim / physics sim / real via node swap) |
| Skill representation | Successful recipes reused across Git branches | Persistent skill library (code + diagnosis) | Subgraph + manifest + evalcard, hub-registrable, trust-tiered |
| Safety mechanism | Budgeted trials, safety interfaces (implicit in harness) | Not the focus | Structural: safety classes, budget-guard interposition, unroutable oracle, frozen verifier — H5 is a headline claim |
| Coordination | Git branches per agent | Skill merging | Git worktrees + typed graph diffs + idea tree |
| Cost to reproduce | 8-robot fleet + GPU cluster + large token budget | Moderate | Laptop (Genesis on Metal/CUDA) + API tokens |

### 10.2 Where ENPIRE/ASPIRE are ahead of us (honest cons)

- **Real-world evidence.** ENPIRE's entire contribution is that the loop survives contact with physics — friction, sensor noise, hardware faults. AISLE is sim-first; until Phase 4, every result carries a "in simulation" asterisk, and their own Push-T finding (all agents solved sim, two of three failed on hardware) is a warning aimed directly at us. *Mitigation: perception ladder, behavioral reset parity, verifier-fidelity metric, MuJoCo cross-check — designed so nothing in the loop assumes sim privileges.*
- **Proven headline numbers.** 99% pass@8 on GPU insertion is credibility we don't have. Our tasks (box picking) are mechanically easier; we compensate with harder *system* claims (composition, safety, portability), but the team should expect "your tasks are toys" as a review objection.
- **Upfront schema tax.** ENPIRE's agents start iterating immediately; ours can't move until the registry, manifests, and validator exist. That's real engineering before any research result — and if manifests are wrong, we've constrained agents into a worse action space than free-form code. *This is exactly what ablation A1/A3 and hypothesis H4 test; if the substrate loses, that's a publishable negative result, but budget for it emotionally.*
- **Backing and ecosystem gravity.** They ship with NVIDIA's brand, robot farm, and promised open-source release. If ENPIRE's release lands mid-experiment with a general harness, our differentiation must already be sharp (see 10.3) or we look like a re-implementation.
- **Task-generality of the EN module.** They demonstrated reset/verify engineering across four contact-rich tasks; we have one scene family. Their evidence that "reset is often easier than the task" is broader than ours will be initially.

### 10.3 Where AISLE is ahead (pros)

- **The substrate is the experiment.** ENPIRE treats infrastructure as a means; we treat it as the object of study, with a falsifiable claim (H4: typed dataflow iteration beats script iteration on time-to-success and iteration latency). Nobody has measured this; it's the gap in their papers.
- **Hot-swap iteration.** ENPIRE's robots idle ~50% while agents think, and every change is a relaunch. Dynamic dora graphs let agents swap a stage mid-stream and attach live probes — the iteration-latency plot (relaunch vs. hot-swap) is a figure neither NVIDIA paper can produce.
- **Structural safety, not behavioral safety.** Their safety is budgets and hoping agents behave; ours is type-system-shaped: motion-class gating, guard interposition, unroutable oracle topics. "Agents freely author motion code; wrong-medicine rate stays zero *by construction*" (H5) answers the objection that blocks agentic robotics from deployment — and pharmacy framing makes it legible to non-robotics audiences.
- **Auditability.** Every agent change is a diff on a typed graph plus an idea-tree entry; every skill has an evalcard; every trace attributes behavior to a named subgraph version. ENPIRE's audit story is "read the Git history of a script." For any regulated domain (their own Cosmos-H push!) our story is stronger.
- **Reproducibility economics.** Their fleet-scaling study needed eight physical robots; ours needs `n_envs` on one GPU. A laptop-scale, open, ENPIRE-class benchmark is something a university lab or a dora community member can actually run — that's an adoption flywheel NVIDIA's harness won't have even when open-sourced (it presumes their robot farm).
- **Cross-embodiment and environment portability at the graph layer.** Same graph, swap one driver node: Franka ↔ SO-101 ↔ real hardware ↔ world-model env. ENPIRE is bound to its lab setup; ASPIRE skills are code without a typed embodiment contract. The three-tier environment ladder (neural/physics/real behind one topic contract) is architecturally unique to us.
- **Skills with governance.** ASPIRE's library grows but has no trust model. Our evalcards + trust tiers + motion-class ceilings are the first specified governance for agent-authored robot code — independently publishable, and directly relevant to dora-hub's future either way.
- **Model-neutral glue positioning.** ENPIRE/ASPIRE assume the NVIDIA stack end-to-end. AISLE orchestrates GR00T *or* π0 *or* classical pipelines as interchangeable typed nodes — the Switzerland position, which is dora's natural strategic ground.

### 10.4 Complementarity (the slide to end the team discussion on)

Adopt their metrics wholesale (pass@k with in-context-retry semantics, MRU/MTU, time-to-milestone) so results are directly comparable. When ENPIRE open-sources, the right move is not to compete with the harness but to **make dora the best runtime underneath it** — their EN/R modules are a topic contract and a rollout scheduler, which is what dora is. Likewise ASPIRE skills should be importable as subgraph skills with generated manifests. If both integrations land, AISLE's thesis is proven in the strongest possible way: NVIDIA's own auto-research stack running on open middleware.

---

## 11. Retail competition suite (S1–S3)

Three scenarios extend the pharmacy desk into a small retail store. They are deliberately competition-shaped — externally defined success criteria, randomized task assignment, time-scored — which gives the project an outside benchmark and a demo narrative. Architecturally they add two things the desk curriculum lacks: **mobility** and **long-horizon multi-goal planning**. They enter after M0 as the Phase 3+ advanced suite.

### 11.1 The scenarios

**S1 — Product picking.** On a start signal, the robot moves to the delivery counter, reads the takeout order placed there (two product types with name, specification, and quantity each), navigates to the corresponding shelf locations, identifies and retrieves the correct products in the required quantities, and delivers them to the counter. Success = all ordered items on the counter, nothing extra.

**S2 — Shelf restocking.** The robot patrols the shelving area, identifies out-of-stock slots and their product categories (both randomly assigned per episode), retrieves the required products from the restocking bin, and places them into the correct slots, properly positioned and neatly arranged. Success = two products restocked to spec.

**S3 — Inspection and return-to-shelf.** The robot patrols the store, identifies two misplaced products (product identity ≠ slot's assigned category), picks them up, and returns each to its correct slot, properly positioned and neatly arranged. Success = both misplaced items back in their designated slots.

### 11.2 The planogram: one artifact, three verifiers

All three scenarios verify against the same ground truth: a **planogram** (`scenes/planogram.toml`) mapping every shelf slot to its assigned product category, slot template pose, and capacity. S1's "corresponding shelf location," S2's "out-of-stock slot + category," and S3's "misplaced" are all queries against it. The episode generator perturbs the planogram state per seed (S2 removes stock; S3 swaps items), and the oracle verifier diffs live scene state against the planogram — which means all three scenario verifiers are one parameterized verifier. This is the retail suite's engineering economy: three competitions, one ground-truth mechanism.

### 11.3 "Properly positioned and neatly arranged," made testable

The competition phrase becomes a quantitative **placement score** (thresholds in `verifier/placement.toml`, all tunable): position within 2 cm of slot template center; yaw within 10° of template; front face outward; zero overhang past the shelf edge; and neighbor alignment — front edges within 1.5 cm of the slot row's alignment line. An item is "neat" iff all five pass; the verifier reports which criterion failed, feeding the failure taxonomy (`misplaced`, `misaligned`, `overhang` join the existing classes). This is worth doing carefully: placement quality is where agent-authored skills will differentiate, and a fuzzy criterion would let agents argue with the referee.

### 11.4 New capabilities (registry additions)

Mobility: `base-driver-sim` (velocity or waypoint commands; base pose out), `waypoint-nav` (plans collision-free base motion between named store locations), `patrol-planner` (coverage sequence over shelf zones). Perception: `order-reader` (order slip on the counter → structured order JSON; oracle rung publishes the order directly, realistic rung is OCR/VLM on the rendered slip), `stock-detector` (empty-slot detection + category from shelf label), `misplacement-detector` (detected identity vs. planogram). Manipulation: `placement-controller` (slot-template-relative fine placement — the neatness skill), plus the existing grasp stack. Planning: `task-planner` (order/patrol goals → sequenced subtasks; this is where agents will spend most of their iteration). The embodiment axis gains a `mobile` profile: base + arm (contract extension in SPEC 210), with the fixed-base desk profiles unchanged.

### 11.5 Scoring and what the suite buys the research

Scoring is competition-style per episode: binary task success, wall-clock time, and penalties (wrong item delivered, item dropped, placement criteria failed) — reported alongside the standard pass@k so results are comparable both internally and to the external competition. Research-wise the suite is the strongest possible testbed for **H3 (skill accumulation)**: S1, S2, and S3 share navigation, shelf perception, and placement skills almost entirely — an agent that has solved S1 with a persistent library should attack S2 with most of its skills already evalcarded, and the S1→S2→S3 transfer curve becomes the headline accumulation figure, far more convincing than the desk-tier transfers. It also stress-tests the composer: S1 graphs will be 2–3x larger than T1 graphs, probing whether typed composition holds up at realistic scale.
