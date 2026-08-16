# AISLE — Agentic Infrastructure for Safe Learning and Execution

New contributor? Start with the
**[AISLE contributor wiki](docs/contributor-wiki.md)** for a source-linked
project overview, architecture, use cases, extension guide, code map, research
status, and known limitations.

*AISLE is an **environment** in the reinforcement-learning sense — a simulated
world with tasks, dynamics, and a frozen scorer that agents act in — and the
**infrastructure** around it: the typed contract, registry, validator, safety
guard, and evidence harness. The name nods to the pharmacy aisle where the
first task family lives, but the scope is the substrate, not the store: the
bench suite (SPEC 300/310) is laboratory powder handling, and hardware
execution is the intended path.*

Agentic auto-research for robot manipulation on open infrastructure:
coding agents (Claude Code / Codex) compose and evolve **typed dora-rs
dataflows** against a **Genesis** physics scene, with frozen
verification/reset and hard safety structure. The claim under test: a
typed dataflow substrate makes agentic robotics faster, safer, more
auditable, and more reusable than script-level iteration — reproducible
on a MacBook.

## Research question

> Can AI coding agents autonomously build, diagnose, improve, reuse, and safely
> operate robotic systems when those systems are composed as typed dora
> dataflows?

AISLE does not treat a successful robot demo as sufficient evidence. The object
under study is the full engineering loop: an agent chooses and connects
capabilities, validates the graph, runs budgeted episodes, diagnoses typed
traces and failure classes, improves the system, and carries evaluated skills
into later tasks. The task result, research cost, safety events, graph and code
identity, environment, seeds, and admissibility audit are recorded together so
we can distinguish an attributable improvement from an easier seed, changed
scorer, environment drift, contamination, or extra compute.

The current model-light runtime is an experimental control, not the intended
limit of the architecture. VLA policies, world-model planners/environments, and
World Action Models (WAMs) can enter as typed, swappable nodes behind the same
action adapters, guard, verifier, and evidence contract. That makes “classical
pipeline vs. learned policy vs. predictive/hybrid system” a matched systems
experiment that the coding agent can itself propose and run.

Read the **[AISLE technical report](docs/AISLE-technical-report.md)** for the
full standalone treatment: architecture, determinism, evidence design, the
experimental program, results to date, threats to validity, and the staged
VLA/world-model/WAM agenda. The **[AISLE research program](docs/research-program.md)** gives the
technical-report framing: research object, falsifiable questions, why
experiments and evidence collection matter, claim discipline, and the staged
VLA/world-model/WAM agenda. The full original experiment design is
[`docs/Project_AISLE_Experiment_Design.md`](docs/Project_AISLE_Experiment_Design.md).
New to the repo? Start with `docs/getting-started.md` — for the
concepts behind it all (Physical AI, VLM/VLA/world models/WAMs,
sim-to-real, agentic auto-research), `docs/physical-ai-primer.md` — and
for the shorthand every other page uses (`CON-5`, `ADR-30`, `H3`, `A7`,
`T2`, `L1`, Class C, DoD, the frozen set),
**[`docs/glossary.md`](docs/glossary.md)**, which expands each identifier
and names the file that defines it.

## Status

**This table is the single current status page** (issue #142). Other overview
pages link here; protocol and evidence pages may retain dated summaries for
context, but must identify their snapshot and defer to this table on conflict.
Status as of **2026-08-16**, commit `0035c45`. Each row states the verdict its
committed evidence supports, with that evidence's own qualifications — a
hypothesis with no admissible data says so rather than reading as progress.

### Phase status (design doc §8)

**Phase 2 (§8.3) and Phase 3 (§8.4) are both closed as of 2026-08-16.**
Full record: [`analysis/reports/phase2_phase3_report.md`](analysis/reports/phase2_phase3_report.md).

| Phase | DoD | Verdict |
|---|---|---|
| Phase 0 — world bring-up | SPEC 090 | signed off (M0) |
| Phase 1 — registry, validator, harness | validator + 12 manifests + H1 table | complete |
| **Phase 2** — the full autoresearch loop | 8 items | **complete** — pass@k curves T1/T2, verifier fidelity, iteration latency, A1/A3/A7, zero unclamped guard violations, post-mortems |
| **Phase 3** — skills, fleet, cross-embodiment | 6 items | **closed: 5 met, 1 NOT MET** — the skill library reached 3 evalcarded skills against a DoD of ≥5 |

The one unmet row is stated plainly rather than rounded. The library holds
`s1-driver-v2`, `s3-driver-v1`, and **`ik-transfer-v2`** — the last a
`safety_class: motion` trajectory skill an agent authored against a
trace-cited collision, evalcard 1.0, reviewed and human-merged (#258). Two
further campaign skills exist and are provenance-verified (#252), but
ADR-37's registry floor refuses `t2-scan-pose` (0.33) and `t2-scan-tsm` (0.0)
on their own evalcards, so **3 was the ceiling and the library reached it**.
**T2/T3 being unsolved at session budgets is why the library is small** — not
agent capability; the reuse mechanism demonstrably works (`s3-driver-v1`
appears verbatim in a desk deliverable, a retail→desk cross-suite transfer).

`ik-transfer-v2` is also the §9.4 trust-tier path completing end to end:
authored by an agent in the governance-critical motion class, shipped with
its own eval suite and regression population, lost to the retention gap,
recovered and provenance-verified, reviewed, then human-merged into the
registry. Exercised once, on the class that matters most.

Exact graph/manifest/CLI/ADR catalogs are generated, never hand-counted:
[`docs/generated/project-inventory.md`](docs/generated/project-inventory.md).
Orientation for contributors: [`docs/contributor-wiki.md`](docs/contributor-wiki.md).

| Milestone | State |
|---|---|
| M0 — verified pharmacy-pick loop (SPEC 090) | signed off; expert graph 0.98 pass@1 over 50 seeds, with the milestone replicate independently re-satisfying the gate |
| H1 — zero-shot composition | measured, target not met: 40/40 schema-valid graphs, but 15% (claude) / 65% (codex) launch zero-shot; single dominant failure is uninstalled hub packages (`analysis/h1/`) |
| H2 — iteration to ≥90% | claude arm **met** held-out (1.0 pass@1); codex arm 0.875 held-out at N=8 (one `dropped`), with dev-side evidence of a ≥0.9 system — see `analysis/h2/` for the full verdict |
| H3 — skill accumulation | **UNDECIDED on both suites; no speedup measured.** Retail (S1→S3): `met: null`, every library-arm cell lost to drift. Desk (T1→T4, `analysis/h3/desk/`): `met: null` under strict admissibility, 13 caveats. The interpretable direction, stated with that caveat — on T4, the only tier where both arms produced clean first-success numbers, the ratio is **~1.03** (L 894 s vs W 872 s: parity, not ≤0.5); T2/T3 the library did not rescue what wiped sessions could not do either. **The finding is the ladder's difficulty spacing, not the library**: T1/T4 are easy for both arms (no headroom for a speedup) and T2/T3 are beyond both (no success to speed up), so the transfer curve never got a tier that could show an effect. Skill reuse itself is verified live |
| H4 — hot-swap vs relaunch iteration | **measured at T0**, phase-randomized (ADR-h4 rev 2): hot-swap median iteration latency 32.4 s vs relaunch 41.8 s (ratio 1.29), n=6 per path, zero infra failures. Extremes overlap; no significance or equivalence claim at n=6. UNATTESTED dev measurement — makes no reproducibility claim (`analysis/h4/`) |
| H5 — zero wrong-object under free iteration | **holding, and the denominator has grown substantially.** 0 wrong-object in 224/224 episodes across the three H2 campaign runs (`analysis/h2/`), and 0 across every subsequent campaign: the desk-H3 ladder, A3's two arms, A4's two agent CLIs, and **all 13 A5 fleet lanes under 8-way concurrent agent-authored iteration**. Roughly 40 agent sessions have now authored motion code freely without producing a wrong-medicine delivery. Still a denominator, not an absolute — the structural argument is the guard's, and this corroborates it |
| H6 — agent operates a running system | **registered, not yet run** (August 2026): detect an induced degradation in a live dataflow, localize it, propose a validated hot-swap, recover — no human in the loop, no guard bypass, no wrong-object during the intervention. The inference/operation half of the programme; needs a fault-injection protocol and an ADR before it runs |
| Retail suite S1–S3 (mobile, long-horizon) | implemented: store scene, planogram verifier, mobility contract, S1 expert graph |
| Perception ladder L0/L1/L2 (TC-9) | implemented: L0 oracle poses, L1 segmentation + depth (`segmented-pose`), L2 RGB identity + same-stamp sensor-depth geometry (`l2-pose`); the rung rides the graph and is asserted per run (`--perception`) |
| Tier curves T1/T2 (Phase-2 DoD) | T1 expert **1.0** per rung; **T2 expert 0.08** (2/25 seeds) — the deliberate perception wall. The label READ works when parked (seed-3-class layouts read 4/5, margins +0.10..+0.31, **0 wrong reads**); what dominates the failure budget is tour mechanics — 15 `never_grasped`, 7 `collision`, 1 `dropped`. **0 `wrong_object` across all 50 T2 episodes ever run.** Every failure mode is honest: refusal, timeout, collision (`analysis/t2/`) |
| T2/T3 unsolved at session budgets | **standing challenge.** No campaign arm — desk-H3 either arm, A3, A4 — has solved T2 or T3 within a session budget. This is the tiers working as designed (§1 curriculum) and is the single biggest open scientific item |
| Realistic verifier (VER-5) | implemented (`src/aisle/verifier/realistic.py`, OWLv2 + rules, CPU-pinned); ADR at `docs/decisions/ADR-realistic-verifier.md` |
| VER-6 verifier fidelity | current VER-13 fusion recomputed over the same 31 recorded episodes: agreement **0.45**, false SUCCESS **0.00** (0/6), false FAIL **0.68** (17/25). The preserved first, pre-amendment measurement was 0.29 / 0.00 / 0.88 (`analysis/ver6-fidelity/`; current recomputation in SPEC 040 VER-13). Conservative, not yet interchangeable with the oracle |
| CON-5 reproducibility on S1 | **original violation dispositioned**: ADR-25 fixed and verified reset-anchored startup; ADR-26 defines full-episode outcomes as statistical under Metal noise. Issue #71 remains open for wall-coupled command/control timing and possible frozen-set retiming — per-seed outcome flips are not themselves a CON-5 violation |

### Ablations (design doc §6)

Every row is n-limited and says so. These are directional results from single
matched pairs, not powered comparisons.

| Ablation | Result |
|---|---|
| A1 — agent-composed vs expert graph | measured end-to-end (compose, launch, pass), with a composition that never launches scoring 0. An earlier draft conditioned on the 16/40 graphs that launched — selecting away the dominant failure mode A1 exists to measure — and was corrected. The T1 rerun is the attested cell of record (`analysis/a1/a1_table.md`; read its "what the records do NOT support" section first) |
| A3 — params-only vs params+code | **the constrained arm won on efficiency at equal quality** (pin `8af9b47a`): params-only reached first success in 9.8 min vs 13.8, spent **200k tokens vs 396k** (50% vs 99% of budget), 24 min vs 85 min wall, **1 dev rollout vs 4** — and both arms scored 1.0/1.0 held out with 0 `wrong_object`. Params-leak audit on arm P: clean. Reading: **schema-as-subsidy** where the registry already covers the task. n=1 per arm on the easiest tier (`analysis/a3/`) |
| A4 — Claude Code vs Codex | **both solve T1 outright**, 1.0/1.0 held out, 0 `wrong_object` (pin `cb814e12`, identical budgets/prompt/seeds). Codex reached first verified success sooner (**8.1 vs 9.7 min**) then kept iterating; Claude converged in 2 rollouts and stopped. End-to-end cost: **186k vs 364k tokens**, 36 vs 73 min. The difference is style, not capability; Claude's session was ~2× cheaper at equal quality. n=1/arm, lower bound (`analysis/a4/`). Kimi Code out of scope v1 |
| A5 — 1 vs 4 vs 8 agents (fleet scaling) | **throughput saturates at ~4 lanes on one host**: 1.6 → 4.1 → **4.3** successes/hour at N=1/4/8. Going 4→8 bought +5% throughput for 2× the agents and 2.2× the token burn. Per-agent latency degrades gracefully (median first success 10.5 → 14.1 → 18.0 min); **quality is contention-invariant** (holdout 1.0 on every lane); token super-linearity +22%/+31% per agent. 0 `wrong_object` across all 13 lanes. Deviation from §8.4.3 recorded in the ADR: lanes share the host with their own sim, not one batched bridge (`analysis/a5/`) |
| A6 — teleport vs behavioral reset | **teleporting hides a real task and a real cost.** Paired 10-episode T1 arms, seeds 0..9: teleport **1.00** pass@1 in 6.4 min; behavioral **0.80** (2 `never_grasped`) in 9.6 min, at **+19 s per episode**. Reset outcomes on the behavioral arm: 7 success / 3 audited `fallback: true` — i.e. the reset is itself a manipulation task that fails sometimes, which is exactly ENPIRE's claim and what a teleport inner loop conceals. 0 `wrong_object` in both arms (`analysis/a6/`) |
| A7 — oracle vs realistic verifier driving the loop | verifier-driven loop measured; budgets re-derived. See the VER-6 row for the fidelity number the ablation depends on, and note the rung caveat below |

### Governance findings (§8.4 review, 2026-08-15/16)

Running the Phase-3 agent-PR review to completion surfaced **three harness
defects and zero agent transgressions**. Recorded because that asymmetry is
itself the result — a review of agent-authored robot code produced no true
positives against the agents and three against the machinery that reviews them.

| Finding | Disposition |
|---|---|
| The skill eval floor was **self-graded** — `min_pass_rate` came from the candidate's own `eval.yaml`, so a skill shipping 0.0 registered at 0.0 and the gate reported `ok` | fixed: `REGISTRY_MIN_PASS_RATE = 0.5`, refused at load before an eval rollout is spent (**ADR-37**, #243) |
| Campaign deliverables were **never archived** — worktrees live under gitignored `runs/` with no retention step, so a campaign record could name a skill no reviewer could find | fixed forward: each session archives its working tree to `refs/campaign/<name>` (#245). The three affected skills were later recovered by hand from the campaign machine and provenance-verified (#252) |
| At perception rung **L2 the policy and the realistic judge would share the detector backbone**, so agreement would overstate independence | guarded: every fidelity report now carries a `backbone` verdict, persisted beside the rates, and fails closed on an unresolved rung (#248). **No reported number is affected** — every VER-6 measurement to date ran at L0, where the policy calls no detector |
| A proposed validator rule (VAL-9) banning policy nodes from importing the judge's detector | **dropped** (#244): the curated core does the same thing, so the rule would have rejected two frozen expert graphs — and the flagged skill never had the alleged import at all (#253) |

Signed review notes: [`analysis/reports/agent_pr_review_notes.md`](analysis/reports/agent_pr_review_notes.md).
The first pass could only review **2 of 5** skills, which any claim about
human-in-the-loop governance of agent-authored robot code has to carry.

## Quickstart

macOS arm64, Python >= 3.11, [uv](https://docs.astral.sh/uv/), and a
Rust toolchain (for the dora CLI). Details and troubleshooting:
`docs/getting-started.md`.

```bash
uv sync --extra sim        # plain `uv sync` REMOVES the sim extras
                           # NVIDIA host? use --extra cuda (GPU torch)
cargo install --git https://github.com/dora-rs/dora --rev cd597e705 dora-cli --locked
dora --version             # warns if CLI and python API revs drift

uv run pytest -m unit                            # fast, no simulator (~90 s)
uv run harness validate graphs/expert_t0.yaml    # typed-graph validation
uv run harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local
# With `uv sync --extra cuda`, add `--sim-extra cuda` (Linux only).
```

dora runs **from source** at the rev pinned in `pyproject.toml`
`[tool.uv.sources]`; the CLI must be cargo-installed from the same rev.
Never install with bare pip/conda.

## Repository map

```
CLAUDE.md          development-agent contract (read first if you are an agent)
specs/             numbered specs with MUST IDs (000 = constitution)
TASKS.md           implementation order + kickoff prompts
registry/          capability schema + typed node manifests
graphs/            expert baseline dataflows (T0/T1 desk pick, S1 retail)
src/aisle/         scenes, bridge, verifier, reset, harness, mobility, nodes
harness CLIs       `uv run harness {validate,rollout,traces,report,skill,swap,probe}`
tools/             CI, trace_check, env_hash, campaign runners (H1/H2/H3/H4)
tests/             unit / sim / graph markers; every MUST cited by a test
analysis/          committed experiment findings: hypotheses (h1..h4),
                   ablations (a1, a3, a4, a5, a6), tiers (t2, t3, t4),
                   ver6-fidelity, s1-determinism, postmortems, transcripts,
                   reports/ (phase2_phase3_report, agent_pr_review_notes)
skills_pending_review/  campaign skills recovered but NOT registered
                   (t2-scan-pose, t2-scan-tsm — both refused by ADR-37's floor)
docs/              guides, design doc, contributor wiki, decisions/ (ADRs),
                   generated/project-inventory.md (source-derived catalogs)
runs/              gitignored: traces, videos, run manifests
```

Structural counts (graphs, manifests, CLI commands, ADRs) are deliberately
absent here — they went stale faster than anyone noticed. The generated
appendix carries them and CI fails when it drifts.

## How development works

Spec-driven, tests-first: specs define WHAT with numbered MUSTs; every
MUST you implement needs a test citing its ID (`tools/trace_check.py`
enforces this in CI); agents implement tasks from `TASKS.md` under the
`CLAUDE.md` contract; humans review Class C paths (CODEOWNERS) and sign
milestones. Gates before every commit: `ruff format --check`,
`ruff check`, `pytest -m unit`, trace_check. Conventional commits; one
concern per PR.

The local CI script also checks requirement traceability, the generated
contributor inventory, and the committed frozen-environment hash.

The experiment's integrity rules are structural, not behavioral: the
environment/verifier/reset set is hash-frozen (rollouts refuse to start
on drift), `oracle_state` cannot be routed to policy nodes, all motion
passes through the budget guard, and research agents operate under a
separate contract (`harness/CLAUDE.research.md`) with idea-tree logging.

See `docs/development-workflow.md` for the full loop and
`docs/architecture.md` for a tour of the system.
