# Notation and glossary

**AISLE** — *Agentic Infrastructure for Safe Learning and Execution*. Each
letter is load-bearing: **Infrastructure** is the contribution (typed contract,
registry, validator, guard, evidence harness), **Safe** names the
non-bypassable safety structure that H5 measures, **Learning** is the research
loop (H1–H4), and **Execution** is operating a running system (H6). The word
also nods to the pharmacy aisle of the first task family. Earlier expansions —
"Project Apothecary", then "Agentic In-Store Learning Environment" — were
outgrown as the scope widened; the lineage is recorded in
[`Project_AISLE_Experiment_Design.md`](Project_AISLE_Experiment_Design.md).

### "Learning", defined (issue #269)

The word misleads newcomers, so it is worth stating exactly. **There is no
training code in this repository** — no `.backward()`, no optimizer, no
`def train(` anywhere in `src/` or `tools/`. Four distinct things wear the
word, and only the last is the conventional one:

1. **Outer-loop search by the coding agent** — the primary sense. Compose,
   validate, roll out, read traces, revise. The search operator is an LLM
   editing YAML and Python; the signal is the validator's error messages, which
   this repository twice calls "the research agent's learning signal".
2. **Accumulation** — the evalcarded skill library that outlives a session
   (H3).
3. **The reinforcement-learning sense** — AISLE as the *environment* a learner
   acts in: episode boundaries, seeded resets, a frozen scorer. AISLE does not
   learn here; it is what you learn against. The verifier emits a status enum
   plus a failure class, **never a scalar reward**.
4. **Learned models** — VLA/VLM/world-model nodes (design doc §7.5). Live since
   the SmolVLA bring-up, and inference-only: weights pinned by revision hash,
   loaded under `torch.no_grad()`.

**"Safe" is structural, not aspirational.** Whatever is learning, the tracked
verifier and reset artifacts are frozen (CON-7), while oracle isolation
(VAL-6) and declared motion-path gating (VAL-5) are statically checked. The
registry floor (ADR-37) and held-out seeds encode a rule specific to learning:
*a learner may not self-certify what it accumulates.* None of these statements
alone establishes a process-wide bypass boundary.

The operating definition:

> **Learning in AISLE is a change to the system that is recorded,
> attributable, and re-runnable.** An improvement that leaves no such artifact
> does not count as learning here, because it is indistinguishable from a lucky
> seed.

Long version with worked examples: technical report
[§3.5](AISLE-technical-report.md#35-what-learning-means-in-aisle) and
[§7.6](AISLE-technical-report.md#76-the-scorer-is-not-a-reward-function).

Every identifier this repository uses, expanded, with the file that defines
it normatively. If a definition here ever disagrees with the spec it cites,
**the spec is right and this page is stale** — same rule the README's status
table has (issue #142).

The standalone [technical report](AISLE-technical-report.md) uses this
notation throughout and is the best single place to see it in context.

New to the project? Read [`getting-started.md`](getting-started.md) for the
environment, [`../README.md`](../README.md) for what AISLE is and why, and
[`physical-ai-primer.md`](physical-ai-primer.md) for the field concepts
(Physical AI, VLM/VLA/world models/WAMs, sim-to-real). This page is the
decoder ring for the shorthand those documents use.

---

## 1. How to read an identifier

Four kinds of identifier appear throughout the specs, code comments, commit
messages, and PR descriptions. They are not interchangeable.

| Form | Example | What it is | Where it is defined |
|---|---|---|---|
| `PREFIX-N` | `CON-5`, `TC-2`, `VER-3` | A **requirement**. Normative, testable, cited by the test that proves it. | `specs/*.md` |
| `ADR-N` | `ADR-30` | A **decision record**. Explains why one interpretation was chosen. | `docs/decisions/ADR-*.md` |
| Letter+digit | `H3`, `A7`, `T2`, `S1`, `L1`, `M0` | An **experiment, tier, rung, or milestone code**. | `docs/Project_AISLE_Experiment_Design.md`, `docs/experiments.md` |
| `Class A/B/C` | `Class C` | A **change-risk class** that decides which gates a PR must pass. | `specs/000-constitution.md` CON-10 |

The requirement grammar is deliberate. `specs/010-topic-contract.md` is
"SPEC 010", its requirements are `TC-1`, `TC-2`, …, and a test that proves
`TC-2` cites `TC-2` in its docstring. `tools/trace_check.py` fails CI when a
MUST has no citing test, so the identifiers are load-bearing rather than
decorative — they are the join key between a rule, its test, and the PR that
implemented it.

---

## 2. Requirement-ID prefixes

One prefix per specification. The prefix is an abbreviation of the spec's
subject, not an acronym of its title.

| Prefix | Expansion | Spec | Governs |
|---|---|---|---|
| `CON` | **Con**stitution | [`specs/000-constitution.md`](../specs/000-constitution.md) | Project-wide invariants: determinism, the frozen set, risk classes, gates, commit and PR discipline. Overrides everything else. |
| `TC` | **T**opic **C**ontract | [`specs/010-topic-contract.md`](../specs/010-topic-contract.md) | The driver-level wire contract: topic names, Arrow schemas, rates, mandatory metadata, and the service/action patterns. What makes sim→real a node swap. |
| `SCN` | **Sc**e**n**e | [`specs/020-scene.md`](../specs/020-scene.md) | The Genesis pharmacy scene: geometry, med definitions, seeded layout, label textures. |
| `BRG` | **Br**id**g**e | [`specs/030-bridge-node.md`](../specs/030-bridge-node.md) | The dora↔Genesis bridge node that owns the simulated scene and steps physics. |
| `VER` | **Ver**ifier | [`specs/040-verifier-reset.md`](../specs/040-verifier-reset.md) | Episode judging: the oracle verifier, the realistic (perception-based) verifier, the failure taxonomy, and evidence sidecars. |
| `RST` | **R**e**s**e**t** | [`specs/040-verifier-reset.md`](../specs/040-verifier-reset.md) | Episode reset: teleport mode and behavioral mode (the robot physically restores the scene). Shares a spec file with `VER`. |
| `CAP` | **Cap**ability | [`specs/050-capability-schema.md`](../specs/050-capability-schema.md) | The capability manifest schema and the registry: how a node declares what it provides, requires, and was evaluated at. |
| `VAL` | **Val**idator | [`specs/060-validator.md`](../specs/060-validator.md) | Static graph validation: typed edges, oracle-leak prevention, motion gating, install checks. Its error messages are the research agent's learning signal. |
| `HAR` | **Har**ness | [`specs/070-harness-clis.md`](../specs/070-harness-clis.md) | The CLIs: `rollout`, `traces`, `report`, `registry`, `trace_check`. Episode execution, evidence capture, and metric semantics. |
| `BG` | **B**udget **G**uard | [`specs/080-budget-guard.md`](../specs/080-budget-guard.md) | The safety node every motion command must traverse: velocity/keep-out clamping, watchdogs, violation reporting. |
| `M0` | **M**ilestone **0** | [`specs/090-milestone-M0.md`](../specs/090-milestone-M0.md) | The Phase-0 exit criteria. `M0-1`…`M0-6` are milestone requirements, not a spec subject. |
| `RS` | **R**etail **S**cenarios | [`specs/200-retail-scenarios.md`](../specs/200-retail-scenarios.md) | The S1–S3 retail competition suite. |
| `MOB` | **Mob**ility | [`specs/210-mobility-contract.md`](../specs/210-mobility-contract.md) | The mobile-base profile: base topics, navigation as a dora action, arm/base mutual exclusion. |
| `PW` | **P**o**w**der | [`specs/300-powder-scenarios.md`](../specs/300-powder-scenarios.md) | The powder transfer and weighing bench family (P0–P4). |
| `FT` | **F**orce/**T**orque | [`specs/310-force-balance-contract.md`](../specs/310-force-balance-contract.md) | Wrist force/torque sensing topics. |
| `BAL` | **Bal**ance | [`specs/310-force-balance-contract.md`](../specs/310-force-balance-contract.md) | Laboratory balance (scale) topics for the powder suite. |
| `TOOL` | **Tool** changer | [`specs/310-force-balance-contract.md`](../specs/310-force-balance-contract.md) | Tool-change service contract. |

**`TC-A1`, `TC-A2`, `TC-A3`** are a separate series inside SPEC 010: the
*acceptance* tests for the topic contract. The `A` means acceptance, and it is
unrelated to the `A1`–`A7` ablations in §4.

---

## 3. Decision, process, and governance vocabulary

### ADR — Architecture Decision Record

A numbered, dated record of one decision: the problem, the choice, the
alternatives rejected, and the consequences. Lives in
[`docs/decisions/`](decisions/). The repository has 39 of them.

ADRs exist because of `CON-15`: when a spec is ambiguous, an implementing
agent must **pick an interpretation, record an ADR, and proceed** rather than
stall or silently guess. The ADR is how a later reader recovers the reasoning
that the code alone cannot carry.

Two naming forms appear:

- `ADR-<n>.md` — numbered sequentially (`ADR-30.md`).
- `ADR-<slug>.md` — named for a campaign or protocol (`ADR-h3-campaign-protocol.md`).

An ADR's **Status** line is normative about its own force: `PROPOSED` means it
is a review vehicle, `RATIFIED`/`ACCEPTED` means the owner merged the PR
carrying it, which is the human review `CON-10` requires for a Class C change.

### DoD — Definition of Done

The exit criteria for a project phase: the concrete artifacts and measurements
that must exist before the phase counts as complete. Stated per phase in
[`Project_AISLE_Experiment_Design.md`](Project_AISLE_Experiment_Design.md)
(for example, "DoD (Phase 2): pass@1/pass@8-over-wallclock curves for T1/T2;
verifier-fidelity number; iteration-latency comparison; A1/A3/A7 tables; zero
budget-guard unclamped violations").

DoD is about *phases*, not individual PRs. A PR's completion bar is its
requirement IDs plus the `CON-9` gates.

### Class A / Class B / Class C — change-risk classes

Defined by `CON-10`, adapted from dora's agentic QA policy. The class decides
which gates a change must pass before merge:

| Class | Scope | Required |
|---|---|---|
| **Class A** | Docs, tests, tools | Baseline gates (format, lint, unit tests). |
| **Class B** | Nodes, harness | Baseline gates **plus** the affected acceptance tests. |
| **Class C** | Anything in the **frozen set**; any contract change | **Human review REQUIRED before merge.** |

The letters are a severity ordering, not abbreviations. Class C is why some
PRs in this repo sit open awaiting the owner's merge: that merge *is* the
required human review.

### The frozen set

The paths protected after milestone M0. Changing any of them makes a PR
Class C and requires regenerating the attestation hash with
`tools/env_hash.py --write`.

The set exists so that a measured result cannot be improved by quietly making
the task easier, the scorer friendlier, or the safety envelope wider. Scene,
verifier, reset, expert baselines, robot assets, limits, and budgets are
exactly the things an optimizing agent would otherwise be tempted to edit.

**The operative list is `tools/env_hash.py`, and it is wider than `CON-7`'s
prose.** What the attestation hash actually covers:

| Path | Frozen because |
|---|---|
| `src/aisle/scenes/` | The task itself — geometry, med definitions, seeded layout. |
| `src/aisle/verifier/` | The scorer. |
| `src/aisle/reset/` | Episode initial conditions. |
| `graphs/expert_*.yaml` | The hand-written baselines agent graphs are compared against. |
| `assets/so101/` | Robot model assets. |
| `env/` | Limits and environment configuration. |
| `src/aisle/nodes/budget_guard.py` | The safety envelope (SPEC 080). |
| `harness/budget.toml` | Campaign ceilings (ADR-21). |

`CON-7`'s text names only the first four. The extra four are justified in
`env_hash.py`'s own comments (SPEC 080 for the guard and limits, ADR-21 for
the budgets) and are genuinely hashed, so a change to any of them does
invalidate an attestation — but an agent reading only `CON-7` would not know
that `env/` or `harness/budget.toml` are Class C. Treat `env_hash.py` as
authoritative and expect `CON-7` to be widened by a future `spec-change`.

### PR labels that change the process

| Label | Meaning |
|---|---|
| `spec-change` | Edits `specs/`. Required by `CON-14`; specs are never edited silently. |
| `env-change` | Touches the frozen set. Class C; needs the env-hash update and human review. |
| `spec-conflict` | Opened when a spec and a test disagree. `CON-13` says: **stop**, do not pick a winner unilaterally. |

---

## 4. Experiment codes

### H — Hypotheses

The falsifiable claims the project exists to test. Canonical status lives in
the [README status table](../README.md#status); protocol and evidence detail
in [`experiments.md`](experiments.md).

| Id | Claim |
|---|---|
| `H1` | Zero-shot graph composition by a coding agent reaches ≥80%. |
| `H2` | The evaluate-and-improve loop reaches ≥90% pass@1. |
| `H3` | A persistent, evaluated skill library makes later tasks ≥2× faster. |
| `H4` | Hot-swap iteration beats full relaunch. |
| `H5` | Wrong-object outcomes stay at zero under free agent iteration. |

`H5` is a *safety* hypothesis and is stated as a quantity that must stay at
zero, which is why it is reported as "holding on committed evidence" with an
explicit denominator rather than as a percentage.

### A — Ablations

Controlled contrasts that isolate one mechanism. `A` is for **ablation**.
Defined in `Project_AISLE_Experiment_Design.md` §"Ablations".

| Id | Contrast | Question |
|---|---|---|
| `A1` | Agent-composed graph vs. hand-written expert graph | Is there a composition tax, or a gain? |
| `A2` | Skill library on vs. off across tiers | The mechanism behind `H3`. |
| `A3` | Params-only vs. params + code authorship | How much authorship freedom does the agent need? |
| `A4` | Claude Code vs. Codex vs. Kimi Code | Agent comparison at fixed task and budget. |
| `A5` | 1 vs. 4 vs. 8 agents on batched environments | Fleet scaling; checks for token super-linearity. |
| `A6` | Teleport vs. behavioral reset | What does teleporting the scene back hide? |
| `A7` | Oracle vs. realistic verifier **driving the loop** | Does the portable verifier's noise break learning? |

`A7` is also the name of a runtime mode, because its treatment arm is
implemented as one: `harness rollout --verifier realistic` makes the realistic
verifier's verdict drive episode advancement while the oracle is held out for
scoring. `--verifier both` runs the realistic verifier as a passive sidecar
instead, changing no control flow. That distinction matters — only the first
is the A7 treatment.

### Campaign arms: `L/` and `W/`

In H3 campaign records, a cell is written `<arm>/<tier>`:

- `L/S2` — **L**ibrary arm (skill library persisted) at tier S2.
- `W/S2` — **W**iped arm (library cleared between tiers) at tier S2.

### Tiers — task difficulty

| Family | Codes | Domain |
|---|---|---|
| Desk | `T0`–`T4` | The pharmacy desk curriculum. `T0` pick a known box at a fixed pose; `T1` pick the *named* med among 5 at randomized poses; `T2` identify by *label text only* (no color prior); `T3` target occluded behind another box (needs a rearrangement subtask); `T4` full request loop with confirm-back and "that's the wrong one" recovery. |
| Retail | `S1`–`S3` | The retail store competition suite (SPEC 200). |
| Powder | `P0`–`P4` | The powder transfer and weighing bench family (SPEC 300). |

The tier also selects wall/sim budgets in the harness — retail episodes run
far slower than desk ones, so they carry their own per-episode budgets.

### L0 / L1 / L2 — perception rungs

How much privileged information the policy may see. Enforced statically by the
validator (`VAL-8`) and at runtime by the bridge, so a run cannot quietly
consume ground truth while reporting a higher rung.

| Rung | The policy may use | Forbidden |
|---|---|---|
| `L0` | Simulator ground-truth object poses (`poses`) | — |
| `L1` | Segmentation + depth to estimate pose | `poses` |
| `L2` | RGB + depth only; identity from pixels | `poses`, `seg_overhead` |

`oracle_state` is privileged truth and is available to `verifier-*` nodes at
**every** rung — a verifier that could not see privileged state could not
judge. That is `VAL-6`, and it is deliberately not a rung question.

### M — Milestones and phases

`M0` is both a milestone and a spec prefix (SPEC 090): the Phase-0 exit gate,
after which the frozen set is protected. `M1`, `M2`, `M3` appear as later
phase markers in the experiment design.

`M1`–`M6` are **also** used in [`research-program.md`](research-program.md)
§6 for the model-research questions (when does a learned policy add value,
does predictive planning improve action selection, and so on). Context
disambiguates: a milestone `M` is a delivery gate with a spec, a model
question `M` sits in a table of treatments and outcome measures. If you need
to be unambiguous in prose, write "milestone M1" or "model question M1".

### W1, W2, W3 …

Week markers inside the phase plans in the experiment design. Unrelated to the
`W/` campaign-arm prefix above — context disambiguates.

---

## 5. Runtime and evidence vocabulary

| Term | Meaning |
|---|---|
| **Episode** | One `reset → goal → rollout → verdict` cycle. The unit of measurement. |
| **Graph / dataflow** | A dora YAML file declaring nodes and their typed edges. |
| **Node** | One process in a dataflow. Swappable if it honors the topic contract. |
| **Capability** | A typed, discoverable node contract recorded in a registry manifest. |
| **Evalcard** | Measured evaluation statistics attached to a capability, so reuse is evidence-based. |
| **Skill** | An agent-authored, evalcarded capability admitted to the registry via `harness skill register`. |
| **Oracle** | Privileged simulator truth. Policy access is restricted; verifier access is not. |
| **Realistic verifier** | A perception-based judge that sees only what a real deployment would. Its agreement with the oracle is the *fidelity* metric. |
| **Sidecar** | Per-stage verifier evidence recorded alongside oracle outcomes, without affecting control flow. |
| **Idea gate** | The requirement that a hypothesis be logged **before** the run that tests it, so post-hoc storytelling is structurally prevented. |
| **Attestation** | Evidence that code, graph, dependency selection, runtime, and baseline all match the claimed treatment. |
| **Trusted run** | A run admitted and post-audited against its protocol's trusted baseline. Untrusted runs can be diagnostic, never verdict evidence. |
| **Frozen set** | See §3. |
| **Hot swap** | Validated replacement of a node in a live dataflow, without restarting the run. |
| **rtf** | **R**eal**t**ime **f**actor: simulated seconds per wall second. Desk ≈ 0.5, retail ≈ 0.07. Budgets that assume one silently break on the other. |
| **pass@1** | Fraction of episodes succeeding on the first attempt. |
| **pass@8** | Fraction succeeding within ≤8 **in-context retries within one episode** (`HAR-3`). Explicitly **never** best-of-8 independent episodes. |
| **Wall clamp** | The per-episode wall-time kill that stops one wedged episode from consuming a whole run's window (ADR-23). Recorded as a synthetic `wall_clamp` failure, distinct from the verifier's sim-time `timeout`. |
| **Failure taxonomy** | The closed set of failure classes (`VER-3`): `wrong_object`, `dropped`, `timeout`, `never_grasped`, `collision`. `wrong_object` is the one the safety hypothesis tracks. |

### Model-family acronyms

Expanded in [`physical-ai-primer.md`](physical-ai-primer.md), summarized here:

| Acronym | Expansion |
|---|---|
| `VLM` | **V**ision-**L**anguage **M**odel |
| `VLA` | **V**ision-**L**anguage-**A**ction model (a VLM with an action head) |
| `WAM` | **W**orld **A**ction **M**odel |

These name *candidate inner-loop policies*. The current runtime is
deliberately model-light: that is an experimental control, not a limit of the
architecture. Any of them can enter as a typed node behind the same action
adapters, guard, verifier, and evidence contract.

---

## 6. Where the normative answer lives

When two documents disagree, this is the precedence order:

1. **`specs/000-constitution.md`** — overrides every other document.
2. **The other `specs/*.md`** — normative requirements, cited by tests.
3. **`CLAUDE.md`** — the development contract for agents working in this repo.
4. **`docs/decisions/ADR-*.md`** — why a particular interpretation was chosen.
5. **The README status table** — canonical experiment status (issue #142).
6. **Everything else in `docs/`** — explanatory; stale until proven otherwise.

Generated inventories under [`docs/generated/`](generated/) are derived from
source by `tools/docs_inventory.py` and are never hand-edited.
