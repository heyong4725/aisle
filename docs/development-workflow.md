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

First, a review pass on the diff and a simplification pass (in Claude
Code: `/review` then `/simplify`; solo humans: self-review the full
diff with the same intent). Findings are gate failures, not
suggestions. Then the mechanical gates:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -m unit
uv run python tools/trace_check.py
uv run python tools/docs_inventory.py --check
uv run python tools/env_hash.py --check
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

## Risk classes and the frozen set

Risk classes per CON-10:

- **Class A** — docs, tests, tools: baseline gates.
- **Class B** — nodes, harness: baseline gates + the affected
  acceptance tests.
- **Class C** — anything in the frozen set, and contract changes:
  human review REQUIRED before merge (CODEOWNERS enforces). Spec edits
  additionally go through a `spec-change` PR (CON-14).

The **frozen set** (CON-7) is exactly: `src/aisle/scenes`,
`src/aisle/verifier`, `src/aisle/reset`, and `graphs/expert_*.yaml`.
After M0 sign-off, changing any of it requires a human-merged PR
labeled `env-change`, with the new `tools/env_hash.py` output committed
alongside. This is the experiment's integrity: rollouts refuse to
start if the hashes drift from the trusted baseline, so a frozen-set
change is always an explicit human decision, never a side effect.

## Rules that bite (learned the hard way)

- **CON-8**: every CLI prints JSON to stdout, logs to stderr, exit 0
  iff ok. Refusals are JSON error values, not stack traces.
- **CON-5**: determinism is a feature. Inject RNG and time; same seed ⇒
  same result. No `Date.now()`-style ambient state in result paths.
  (See `determinism.md` for the sim-side story.)
- **CON-2**: uv only, no bare pip/conda. **CON-1**: CUDA-only deps are
  forbidden in default extras (Metal/MPS is the primary platform).
- Sim runs are machine-exclusive, and a killed run can leak simulator
  processes — see `troubleshooting.md` before debugging any "slow" or
  flaky sim result.
- Research campaigns have extra integrity rules (commit pinning,
  held-out seeds, token ceilings) — see `experiments.md`. Never hand a
  research agent a worktree containing committed analysis of the
  experiment it is running.

## Review

Beyond the per-commit review gate above, Class C changes need the code
owner. Campaign-affecting changes (`tools/campaign.py`,
`tools/h3_campaign.py`, harness) deserve an adversarial pass: the
reviewer's job is to break the integrity story, not admire it.

## Documentation freshness

Structural facts drift silently; qualitative claims drift worse, because
nothing fails when they do. Issue #142 found five orientation pages
answering the same question differently — one said the realistic verifier
was pending while the code and its fidelity measurement were committed.

Two rules keep that from recurring:

1. **Never hand-maintain a structural count.** Graph, manifest, CLI and
   ADR catalogs are generated into `docs/generated/project-inventory.md`
   and `tools/docs_inventory.py --check` fails CI on drift. Do not write
   "26 manifests" in prose — link the appendix. The JSON report from
   `uv run python tools/docs_inventory.py --check` includes the tracked
   `test_modules` count; `uv run pytest --collect-only` reports collected
   test cases. For requirement coverage, run `uv run python
   tools/trace_check.py`.
2. **One status page.** [The README status table](../README.md#status) is
   the single current source for milestone and hypothesis verdicts. Other
   pages link to it and explain mechanism. Protocol and evidence pages may
   retain a dated status summary when the context needs one, but must stamp
   it "as of <date> (<commit>)" and say the README wins on conflict.

Checklist when a hypothesis verdict changes, a Phase-2 item lands, or a
finding is committed to `analysis/` — the claims below cannot be generated,
so a human owns them:

- [ ] README status table updated, with the evidence's OWN qualifications
      carried over (unattested, undecidable, n too small, drift-flagged).
      A pending verdict must not read as progress.
- [ ] `docs/experiments.md` protocol/evidence detail agrees with it.
- [ ] `docs/physical-ai-primer.md` "today vs not yet" box still true —
      this page ages fastest, because it describes capability.
- [ ] `docs/architecture.md` describes components that exist.
- [ ] `docs/contributor-wiki.md` maturity table and open-questions section.
- [ ] Any page that is intentionally historical says so at the top
      (see `TASKS.md`).

Preserve committed findings as dated evidence. When a verdict changes,
update the overview that points at the finding — never edit the finding
to match the new story.
