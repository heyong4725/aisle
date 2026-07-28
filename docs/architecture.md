# Architecture tour

A newcomer-altitude walk through the system. Depth on any topic: the
design doc (`Project_AISLE_Experiment_Design.md`, §2–§5) and the spec
that owns it (`specs/`).

## The stack in one picture

```
BRAIN      coding agent (Claude Code / Codex) under a contract
             composes graphs, authors nodes, runs rollouts, logs ideas
ARTIFACTS  git: dataflow YAML, skills/, manifests, evalcards
EVIDENCE   runs/: Arrow traces, videos, results, idea tree
EXECUTION  dora-rs runtime — typed topics, dynamic node add/remove
WORLD      Genesis physics scene (pharmacy desk; retail store)
```

The agent never touches the simulator directly. It edits **artifacts**
(a typed dataflow YAML plus node code), asks the harness to run them,
and reads **evidence** back. Everything in between is enforced
machinery.

## The dataflow layer (dora-rs)

A run is a dora dataflow: nodes connected by typed Arrow topics,
declared in YAML (`graphs/`). The topic contract (SPEC 010,
`src/aisle/topics.py`) fixes schemas, rates, units (radians, base
frame) for every topic class. Two hand-written expert graphs serve as
baselines and integration tests:

- `graphs/expert_t0.yaml` — fixed-pose desk pick (tier T0).
- `graphs/expert_s1.yaml` — mobile retail product-picking (S1).

Every graph passes through the **validator** (SPEC 060) before it runs:
edge-by-edge schema/rate checks, unique ids, every input has a
producer, embodiment consistency — plus the safety-structural checks
below. Errors are machine-actionable JSON with hints; this is the
agent's compile loop.

## The frozen set (the no-cheating rule)

The frozen set (CON-7) is `src/aisle/scenes`, `src/aisle/verifier`,
`src/aisle/reset`, and the expert graphs `graphs/expert_*.yaml` —
hash-manifested by `tools/env_hash.py`, with changes only via
human-merged `env-change` PRs. The rollout runner refuses to start if
hashes drift from the trusted baseline. Agents can read this code;
they cannot change what judges them.

Safety is structural, not behavioral (H5):

- **Oracle isolation** — the bridge publishes a privileged
  `oracle_state` topic (all object poses); the validator refuses any
  graph routing it to anything but the verifier (VAL-6).
- **Motion gating** — every `joint_cmd`/`gripper_cmd` path must pass
  through the `budget-guard` node (SPEC 080), which clamps joint/vel/
  workspace limits and enforces episode timeouts. Only
  `safety_class: motion` nodes may command the arm, and motion nodes
  without an evalcard are refused.
- **Trust anchors** — the budget guard, frozen-set nodes, and `env/`
  code can never be hot-swapped on a live dataflow (`harness swap`
  refuses, HAR-10).

## The world (Genesis)

`src/aisle/scenes/` builds scenes from (seed, embodiment, tier) — a
function, not a script, so reset/batching/randomization are all
reconstructible. The **pharmacy desk** (SPEC 020): shelf, five medicine
boxes, tray, distractors, fixed-base arm. The **retail store**
(SPEC 200): aisles, planogram-governed shelf slots, mobile base +
arm — scenarios S1 (order picking), S2 (restocking), S3 (misplaced-item
return), all verified by diffing live state against
`planogram.toml`.

One bridge node (`dora-genesis`, SPEC 030) owns the simulator and
speaks Arrow on the contract topics. Tiers (T0–T4 desk, S1–S3 retail)
select task difficulty; the perception ladder (L0 oracle poses → L1
ground-truth segmentation → L2 full pixels) selects which pose source a
graph may use.

## Verification and reset (frozen)

The **oracle verifier** (SPEC 040) judges episodes from `oracle_state`:
success plus a failure taxonomy (`wrong_object`, `dropped`, `timeout`,
`never_grasped`, retail adds `misplaced`, `misaligned`, `overhang`).
The asymmetric goal makes `wrong_object` the metric that must stay at
zero. A **realistic verifier** (camera-based, portable to hardware) is
specified (VER-5) with its design brief at
`decisions/ADR-realistic-verifier.md`; fidelity against the oracle is a
first-class result. The realistic verifier is VER-5; the fidelity job
that measures agreement plus false-success/false-fail rates against
the oracle is VER-6. Reset is a service node: `teleport` (state reset)
now, `behavioral` (robot re-shelves the box) later.

## The capability registry

`registry/manifests/*.yaml` — one typed manifest per node: provides/
requires, input/output schemas and rates, params with ranges,
embodiment support, `safety_class`, eval status. 26 manifests cover
the desk stack, the retail additions (base driver, waypoint nav,
patrol, order reader, stock/misplacement detectors, placement
controller, task planner), and system nodes. The registry is what
makes graph composition an LLM-reliable operation — and its deliberate
gaps are experiment probes (uninstalled hub packages were H1's dominant
failure; see `analysis/h1/`).

Skills are the registry's growth path: an agent-authored node or
subgraph + manifest + evalcard, registered via `harness skill register`
(runs the skill's eval suite first). The design intent is for subgraph
skills to nest as single named nodes with trace attribution preserved;
today registered skills are used as single nodes — subgraph NESTING in
the validator/graphs is deliberately deferred (ADR-22 item 6).

## The harness

`uv run harness {validate,rollout,traces,report,skill,swap,probe}` —
every command JSON-to-stdout (CON-8). `rollout` is the workhorse: it
verifies env hashes, validates the graph, launches per-episode, records
traces, and writes a reproducible run manifest (CON-5: same seed, same
result). `swap`/`probe` mutate or inspect a LIVE dataflow (HAR-10..12)
— the H4 iteration-latency mechanism. Full CLI walkthrough:
`harness-guide.md`.

Research agents run under a separate contract
(`harness/CLAUDE.research.md`) with an idea gate: no rollout without an
open idea-tree entry stating hypothesis and expected effect. That log
is what makes a campaign auditable afterward.
