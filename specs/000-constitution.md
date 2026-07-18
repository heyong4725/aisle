# SPEC 000 — Project constitution

Status: STABLE. Changes require human approval (Young). Every other spec inherits this one.

## 1. Project

AISLE: agentic auto-research for robot manipulation on open infrastructure.
Brain = coding agents (Claude Code, Codex). Execution = dora-rs dataflows.
World = Genesis World simulation (pharmacy desk scene). Design doc:
`docs/Project_AISLE_Experiment_Design.md` (v0.3) is the WHY; these specs are the WHAT;
the agent decides the HOW within the constraints below.

## 2. Normative language

MUST / MUST NOT / SHOULD / MAY per RFC 2119. Every MUST has a requirement ID
(`<SPEC>-<n>`) and MUST be covered by at least one test that cites the ID in its
docstring. `tools/trace_check.py` (SPEC 070) enforces this in CI.

## 3. Platform invariants

- CON-1: Primary dev platform is macOS arm64 (M3, 128 GB). All code MUST run on
  Apple Metal / MPS. CUDA-only dependencies MUST NOT enter the default
  dependency set (they MAY live behind an optional `cuda` extra).
- CON-2: Python 3.11+, managed by `uv`. One workspace `pyproject.toml`;
  packages under `src/aisle/`. No conda, no bare pip.
- CON-3: Runtime is dora-rs (installed via `uv tool install dora-rs-cli` or
  cargo). Dataflows are YAML; Python nodes run with `dora run <yaml> --uv`.
- CON-4: All inter-node data is Apache Arrow. No pickle, no JSON blobs on
  hot topics (JSON allowed only in `*_result` / report topics).
- CON-5: Determinism: any run is reproducible from
  `(git_sha, env_hash, platform, seed)`. Nondeterministic APIs (time, RNG)
  MUST be injected, never called ad hoc inside env code.

## 4. Repository invariants

- CON-6: Layout is fixed:
  `specs/` `src/aisle/{scenes,nodes,verifier,reset,harness}/`
  `graphs/` `registry/{schema,manifests}/` `skills/` `tests/{unit,sim,graph,accept}/`
  `tools/` `runs/`(gitignored) `docs/`.
- CON-7: `src/aisle/scenes`, `verifier`, `reset` and `graphs/expert_*.yaml`
  are the FROZEN SET after milestone M0 sign-off: changes require a human-merged
  PR labeled `env-change`, and `tools/env_hash.py` output is committed.
- CON-8: Every tool is a CLI: argparse in, single JSON object on stdout,
  exit code 0 iff `"ok": true`. Logs go to stderr. No interactive prompts.

## 5. Quality gates (inherited and adapted from dora-rs/dora CLAUDE.md)

- CON-9: Before every commit, agents MUST run, in order: `/review` skill on the
  diff; `/simplify` skill; then local CI: `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run pytest -m "unit"` (always) and
  `uv run pytest -m "sim or graph"` when sim/graph code changed. A commit that
  skips gates MUST be reverted, not amended.
- CON-10: Risk classes (adapted from dora `docs/agentic-qa-policy.md`):
  Class A (docs, tests, tools) — baseline gates. Class B (nodes, harness) —
  baseline + affected acceptance tests. Class C (anything in the frozen set,
  contract changes) — human review REQUIRED before merge.
- CON-11: Conventional commits (`feat:`, `fix:`, `test:`, `spec:`); one spec
  concern per PR; PR description MUST list requirement IDs implemented/affected.

## 6. Test taxonomy

- CON-12: pytest markers: `unit` (no sim, no dora, <1 s each), `sim` (imports
  genesis; headless), `graph` (launches a dora dataflow), `accept`
  (spec-acceptance; each cites requirement IDs). Default CI job runs
  `unit`; `sim|graph|accept` run in the nightly job and before any release tag.
- CON-13: Tests are the spec's teeth: when a spec and a test disagree, STOP and
  open an issue titled `spec-conflict: <ids>`; never "fix" the test to pass.

## 7. Agent conduct (development phase)

- CON-14: Agents MUST NOT edit files under `specs/` except via a PR labeled
  `spec-change` with rationale. Specs drive code; code never silently drives specs.
- CON-15: When a spec is ambiguous, the agent MUST record the interpretation it
  chose in `docs/decisions/ADR-<n>.md` (one paragraph) and proceed — do not block.
- CON-16: Two-agent protocol: implementation may be done by Claude Code or
  Codex; the OTHER agent SHOULD be used for review passes (`/review` or
  equivalent). Cross-review findings land as PR comments, not direct pushes.

## 8. Glossary

episode — one reset→rollout→verdict cycle. tier — task difficulty T0..T4.
frozen set — CON-7 paths. oracle — privileged ground-truth state (sim only).
evalcard — measured skill statistics attached to a manifest.
