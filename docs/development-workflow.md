# Development workflow

How changes land in this repo. The authority is
`specs/000-constitution.md` (RFC 2119 MUSTs, CON-* ids) plus
`CLAUDE.md`; this page is the narrative version. It applies to humans
and development agents equally.

## The spec-driven loop

Specs (`specs/NNN-*.md`) define WHAT with numbered MUST ids (VAL-6,
HAR-10, CON-5, ...). For every task:

1. Restate the requirement ids you will satisfy.
2. **Write the acceptance/unit tests first**, each docstring citing its
   ids. `tools/trace_check.py` fails CI if a MUST you implement has no
   citing test (HAR-9).
3. Implement until green.
4. Run the gates (below).
5. PR with the ids listed. One concern per PR (CON-11).

Ambiguous spec? Pick an interpretation, record it as
`docs/decisions/ADR-<n>.md`, and proceed (CON-15) — do not stall. Spec
contradicts a test? Stop and open a `spec-conflict` issue (CON-13).
`TASKS.md` holds the dependency order and paste-ready prompts.

## Quality gates (before every commit)

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -m unit
uv run python tools/trace_check.py
```

Touched sim or graph code? Add `uv run pytest -m "sim or graph"`. CI
(`tools/ci.sh`) runs the same set — if it can fail in CI it should have
failed on your machine first. Chain gates with `&&`, not `;`, so a red
step cannot be scrolled past.

## Git and PR conventions

- Conventional commits: `type(scope): description` — `feat`, `fix`,
  `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.
- **PR titles are commit subjects**: the repo squash-merges, so the PR
  title becomes the mainline commit message and is held to the same
  conventional format.
- Branch names: `feat/...`, `fix/...`, `docs/...`. Never commit to
  main.
- Long commit/PR text goes through `-F/--body-file`, not inline `-m`
  heredocs — backticks in inline messages get shell-executed.
- CI must be green before merge, no exceptions.

## Change classes and protected paths

- **Class A** — tools/, analysis/, docs/ (non-spec): normal PR.
- **Class B** — src/, tests/, graphs/, registry/: normal PR + gates.
- **Class C** — protected (CODEOWNERS): `specs/` (only via a
  `spec-change` PR, CON-14), the frozen set (`env/`,
  `src/aisle/{scenes,verifier,reset}/`, budget guard — only via an
  `env-change` PR with human review, CON-7; the env hash baseline moves
  with it), and CI/governance files.

The frozen set is the experiment's integrity: rollouts refuse to start
if its hashes drift from the trusted baseline. If you legitimately need
to change it, that is an `env-change` PR and a human decision, never a
side effect.

## Rules that bite (learned the hard way)

- **CON-8**: every CLI prints JSON to stdout, logs to stderr, exit 0
  iff ok. Refusals are JSON error values, not stack traces.
- **CON-5**: determinism is a feature. Inject RNG and time; same seed ⇒
  same result. No `Date.now()`-style ambient state in result paths.
  (See `determinism.md` for the sim-side story.)
- **CON-1**: uv only, no bare pip/conda; CUDA-only deps are forbidden
  in default extras.
- Sim runs are machine-exclusive, and a killed run can leak simulator
  processes — see `troubleshooting.md` before debugging any "slow" or
  flaky sim result.
- Research campaigns have extra integrity rules (commit pinning,
  held-out seeds, token ceilings) — see `experiments.md`. Never hand a
  research agent a worktree containing committed analysis of the
  experiment it is running.

## Review

Run a self-review on the diff before every PR (the project treats
review findings as gate failures, not suggestions). Class C changes
need the code owner. Campaign-affecting changes (`tools/campaign.py`,
`tools/h3_campaign.py`, harness) deserve an adversarial pass: the
reviewer's job is to break the integrity story, not admire it.
