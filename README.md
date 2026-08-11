# AISLE — Agentic In-Store Learning Environment

New contributor? Start with the
**[AISLE contributor wiki](docs/contributor-wiki.md)** for a source-linked
project overview, architecture, use cases, extension guide, code map, research
status, and known limitations.

Agentic auto-research for robot manipulation on open infrastructure:
coding agents (Claude Code / Codex) compose and evolve **typed dora-rs
dataflows** against a **Genesis** physics scene, with frozen
verification/reset and hard safety structure. The claim under test: a
typed dataflow substrate makes agentic robotics faster, safer, more
auditable, and more reusable than script-level iteration — reproducible
on a MacBook.

Full experiment design: `docs/Project_AISLE_Experiment_Design.md`.
New to the repo? Start with `docs/getting-started.md` — and for the
concepts behind it all (Physical AI, VLM/VLA/world models/WAMs,
sim-to-real, agentic auto-research), `docs/physical-ai-primer.md`.

## Status

**This table is the single current status page** (issue #142). Every other
overview links here rather than restating a verdict; if they disagree, this
one is right. Status as of **2026-08-10**, commit `0a19154`. Each row states
the verdict its committed evidence supports, with that evidence's own
qualifications — a hypothesis with no admissible data says so rather than
reading as progress.

Exact graph/manifest/CLI/ADR catalogs are generated, never hand-counted:
[`docs/generated/project-inventory.md`](docs/generated/project-inventory.md).
Orientation for contributors: [`docs/contributor-wiki.md`](docs/contributor-wiki.md).

| Milestone | State |
|---|---|
| M0 — verified pharmacy-pick loop (SPEC 090) | signed off; expert graph 0.98 pass@1 over 50 seeds, deterministic replicate |
| H1 — zero-shot composition | measured, target not met: 40/40 schema-valid graphs, but 15% (claude) / 65% (codex) launch zero-shot; single dominant failure is uninstalled hub packages (`analysis/h1/`) |
| H2 — iteration to ≥90% | claude arm **met** held-out (1.0 pass@1); codex arm 0.875 held-out at N=8 (one `dropped`), with dev-side evidence of a ≥0.9 system — see `analysis/h2/` for the full verdict |
| H3 — skill accumulation (S1→S3 transfer) | **verdict PENDING** (`met: null`, `complete: false`) — S2 and S3 both UNDECIDED. No admissible library-arm cell survived the integrity audit (repo, treatment or runtime drift); the wiped arm's clean cells never succeeded at S2/S3. A formal verdict needs an owner-accepted incomplete closure or a budget-corrected new campaign (`analysis/h3/`) |
| H4 — hot-swap vs relaunch iteration | **measured at T0**, phase-randomized (ADR-h4 rev 2): hot-swap median iteration latency 32.4 s vs relaunch 41.8 s (ratio 1.29), n=6 per path, zero infra failures. Extremes overlap; no significance or equivalence claim at n=6. UNATTESTED dev measurement — makes no reproducibility claim (`analysis/h4/`) |
| H5 — zero wrong-object under free iteration | holding on committed evidence: 0 wrong-object in 224/224 episodes across the three H2 campaign runs (`analysis/h2/`) |
| Retail suite S1–S3 (mobile, long-horizon) | implemented: store scene, planogram verifier, mobility contract, S1 expert graph |
| Perception ladder L0/L1/L2 (TC-9) | implemented: L0 oracle poses, L1 segmentation + depth (`segmented-pose`), L2 pixels (`l2-pose`); the rung rides the graph and is asserted per run (`--perception`) |
| Realistic verifier (VER-5) | implemented (`src/aisle/verifier/realistic.py`, OWLv2 + rules, CPU-pinned); ADR at `docs/decisions/ADR-realistic-verifier.md` |
| VER-6 verifier fidelity | first measurement over 31 episodes: agreement **0.29**, false SUCCESS **0.00** (0/6), false FAIL **0.88** (22/25) — the realistic verifier is conservative, not yet interchangeable with the oracle (`analysis/ver6-fidelity/`) |
| CON-5 determinism on S1 | **open defect**: two rollouts at one recorded pin produced different results; scheduler backpressure/startup ordering is the lead candidate (`analysis/s1-determinism/`, issue #71, ADR-25) |

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
