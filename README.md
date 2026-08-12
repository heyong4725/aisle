# AISLE — Agentic Inference, Safety, and Learning Environment

New contributor? Start with the
**[AISLE contributor wiki](docs/contributor-wiki.md)** for a source-linked
project overview, architecture, use cases, extension guide, code map, research
status, and known limitations.

*The name also nods to the pharmacy aisle where the first task family lives —
but the scope is the substrate, not the store: the bench suite (SPEC 300/310)
is laboratory powder handling, and hardware deployment is the intended path.*

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
Status as of **2026-08-10**, commit `0a19154`. Each row states the verdict its
committed evidence supports, with that evidence's own qualifications — a
hypothesis with no admissible data says so rather than reading as progress.

Exact graph/manifest/CLI/ADR catalogs are generated, never hand-counted:
[`docs/generated/project-inventory.md`](docs/generated/project-inventory.md).
Orientation for contributors: [`docs/contributor-wiki.md`](docs/contributor-wiki.md).

| Milestone | State |
|---|---|
| M0 — verified pharmacy-pick loop (SPEC 090) | signed off; expert graph 0.98 pass@1 over 50 seeds, with the milestone replicate independently re-satisfying the gate |
| H1 — zero-shot composition | measured, target not met: 40/40 schema-valid graphs, but 15% (claude) / 65% (codex) launch zero-shot; single dominant failure is uninstalled hub packages (`analysis/h1/`) |
| H2 — iteration to ≥90% | claude arm **met** held-out (1.0 pass@1); codex arm 0.875 held-out at N=8 (one `dropped`), with dev-side evidence of a ≥0.9 system — see `analysis/h2/` for the full verdict |
| H3 — skill accumulation (S1→S3 transfer) | **verdict PENDING** (`met: null`, `complete: false`) — S2 and S3 both UNDECIDED. No admissible library-arm cell survived the integrity audit (repo, treatment or runtime drift); the wiped arm's clean cells never succeeded at S2/S3. A formal verdict needs an owner-accepted incomplete closure or a budget-corrected new campaign (`analysis/h3/`) |
| H4 — hot-swap vs relaunch iteration | **measured at T0**, phase-randomized (ADR-h4 rev 2): hot-swap median iteration latency 32.4 s vs relaunch 41.8 s (ratio 1.29), n=6 per path, zero infra failures. Extremes overlap; no significance or equivalence claim at n=6. UNATTESTED dev measurement — makes no reproducibility claim (`analysis/h4/`) |
| H5 — zero wrong-object under free iteration | holding on committed evidence: 0 wrong-object in 224/224 episodes across the three H2 campaign runs (`analysis/h2/`) |
| H6 — agent operates a running system | **registered, not yet run** (August 2026): detect an induced degradation in a live dataflow, localize it, propose a validated hot-swap, recover — no human in the loop, no guard bypass, no wrong-object during the intervention. The inference/operation half of the programme; needs a fault-injection protocol and an ADR before it runs |
| Retail suite S1–S3 (mobile, long-horizon) | implemented: store scene, planogram verifier, mobility contract, S1 expert graph |
| Perception ladder L0/L1/L2 (TC-9) | implemented: L0 oracle poses, L1 segmentation + depth (`segmented-pose`), L2 RGB identity + same-stamp sensor-depth geometry (`l2-pose`); the rung rides the graph and is asserted per run (`--perception`) |
| Realistic verifier (VER-5) | implemented (`src/aisle/verifier/realistic.py`, OWLv2 + rules, CPU-pinned); ADR at `docs/decisions/ADR-realistic-verifier.md` |
| VER-6 verifier fidelity | current VER-13 fusion recomputed over the same 31 recorded episodes: agreement **0.45**, false SUCCESS **0.00** (0/6), false FAIL **0.68** (17/25). The preserved first, pre-amendment measurement was 0.29 / 0.00 / 0.88 (`analysis/ver6-fidelity/`; current recomputation in SPEC 040 VER-13). Conservative, not yet interchangeable with the oracle |
| CON-5 reproducibility on S1 | **original violation dispositioned**: ADR-25 fixed and verified reset-anchored startup; ADR-26 defines full-episode outcomes as statistical under Metal noise. Issue #71 remains open for wall-coupled command/control timing and possible frozen-set retiming — per-seed outcome flips are not themselves a CON-5 violation |

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
analysis/          committed experiment findings (h1, h2, h3, h4, a1,
                   ver6-fidelity, s1-determinism, postmortems, transcripts)
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
