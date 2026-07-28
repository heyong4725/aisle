# AISLE — Agentic In-Store Learning Environment

Agentic auto-research for robot manipulation on open infrastructure:
coding agents (Claude Code / Codex) compose and evolve **typed dora-rs
dataflows** against a **Genesis** physics scene, with frozen
verification/reset and hard safety structure. The claim under test: a
typed dataflow substrate makes agentic robotics faster, safer, more
auditable, and more reusable than script-level iteration — reproducible
on a MacBook.

Full experiment design: `docs/Project_AISLE_Experiment_Design.md`.
New to the repo? Start with `docs/getting-started.md`.

## Status (July 2026)

| Milestone | State |
|---|---|
| M0 — verified pharmacy-pick loop (SPEC 090) | signed off; expert graph 0.98 pass@1 over 50 seeds, deterministic replicate |
| H1 — zero-shot composition | measured, target not met: 40/40 schema-valid graphs, but 15% (claude) / 65% (codex) launch zero-shot; single dominant failure is uninstalled hub packages (`analysis/h1/`) |
| H2 — iteration to ≥90% | claude arm **met** held-out (1.0 pass@1); codex arm 0.875 held-out at N=8 (one `dropped`), with dev-side evidence of a ≥0.9 system — see `analysis/h2/` for the full verdict |
| H3 — skill accumulation (S1→S3 transfer) | measured campaign in flight (`docs/decisions/ADR-h3-campaign-protocol.md`) |
| H4 — hot-swap vs relaunch iteration | machinery landed (`harness swap` / `harness probe`, SPEC 070 HAR-10..12); experiment queued |
| H5 — zero wrong-object under free iteration | holding: 0 wrong-object in 224/224 episodes across the three H2 campaign runs (`analysis/h2/`); H3 scenario records to date also report 0 |
| Retail suite S1–S3 (mobile, long-horizon) | implemented: store scene, planogram verifier, mobility contract, S1 expert graph |
| Realistic verifier | decision brief at `docs/decisions/ADR-realistic-verifier.md`; implementation pending |

## Quickstart

macOS arm64, Python >= 3.11, [uv](https://docs.astral.sh/uv/), and a
Rust toolchain (for the dora CLI). Details and troubleshooting:
`docs/getting-started.md`.

```bash
uv sync --extra sim        # plain `uv sync` REMOVES the sim extras
cargo install --git https://github.com/dora-rs/dora --rev 7eb4a5f8b dora-cli --locked
dora --version             # warns if CLI and python API revs drift

uv run pytest -m unit                            # fast, no sim (~547 tests)
uv run harness validate graphs/expert_t0.yaml    # typed-graph validation
uv run harness rollout --graph graphs/expert_t0.yaml --tier T0 \
    --episodes 2 --seeds 0..1 --no-idea-gate --env-baseline local
```

dora runs **from source** at the rev pinned in `pyproject.toml`
`[tool.uv.sources]`; the CLI must be cargo-installed from the same rev.
Never install with bare pip/conda.

## Repository map

```
CLAUDE.md          development-agent contract (read first if you are an agent)
specs/             numbered specs with MUST IDs (000 = constitution)
TASKS.md           implementation order + kickoff prompts
registry/          capability schema + 26 node manifests
graphs/            expert baseline dataflows (T0 desk pick, S1 retail)
src/aisle/         scenes, bridge, verifier, reset, harness, mobility, nodes
harness CLIs       `uv run harness {validate,rollout,traces,report,skill,swap,probe}`
tools/             CI, trace_check, env_hash, campaign runners (H1/H2/H3)
tests/             unit / sim / graph markers; every MUST cited by a test
analysis/          committed experiment findings (h1, h2, a1)
docs/              guides, design doc, decisions/ (ADRs)
runs/              gitignored: traces, videos, run manifests
```

## How development works

Spec-driven, tests-first: specs define WHAT with numbered MUSTs; every
MUST you implement needs a test citing its ID (`tools/trace_check.py`
enforces this in CI); agents implement tasks from `TASKS.md` under the
`CLAUDE.md` contract; humans review Class C paths (CODEOWNERS) and sign
milestones. Gates before every commit: `ruff format --check`,
`ruff check`, `pytest -m unit`, trace_check. Conventional commits; one
concern per PR.

The experiment's integrity rules are structural, not behavioral: the
environment/verifier/reset set is hash-frozen (rollouts refuse to start
on drift), `oracle_state` cannot be routed to policy nodes, all motion
passes through the budget guard, and research agents operate under a
separate contract (`harness/CLAUDE.research.md`) with idea-tree logging.

See `docs/development-workflow.md` for the full loop and
`docs/architecture.md` for a tour of the system.
