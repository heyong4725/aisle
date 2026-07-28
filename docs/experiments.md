# Experiments: hypotheses, status, and where results live

Status as of 2026-07-28. Design: `Project_AISLE_Experiment_Design.md`
§6 (hypotheses/metrics/ablations) and §11.5 (retail transfer).
Committed findings live in `analysis/`; raw run data in `runs/`
(gitignored — findings files copy in what the claims need).

## Hypotheses

| Id | Claim | Status |
|---|---|---|
| H1 | Zero-shot graph composition ≥80% | **Measured, not met** — 40/40 schema-valid, but 15% (claude) / 65% (codex) launch zero-shot; the gap is one mechanism: manifests pointing at uninstalled hub packages. Validator now surfaces `INSTALL_MISSING`. → `analysis/h1/` |
| H2 | EN-loop iteration to ≥90% pass@1 | **Met** by two independent arms: claude 1.0, codex 0.875 held-out pass@1 at commit `e8f163ab`. → `analysis/h2/` |
| H3 | Persistent skill library ≥2x faster on later tasks | **Campaign in flight** (S1→S2→S3, arms with/without library). Protocol: `decisions/ADR-h3-campaign-protocol.md`; records land in `runs/h3/`, analysis in `analysis/h3/` when complete. |
| H4 | Hot-swap iteration beats relaunch | Machinery landed (`harness swap`/`probe`, HAR-10..12 event log); measured comparison queued behind H3. |
| H5 | wrong-object stays 0 under free iteration | **Holding** — zero wrong-object across every campaign episode to date (H1+H2: 0 of 224+; H3 so far: 0). |

Ablation A1 (agent-composed vs expert graphs) has a provenance-explicit
data inventory at `analysis/a1/a1_table.md` — read its "what the
records do NOT support" section before quoting numbers; the matched
fill-runs are named there and queued.

## How campaigns are run (integrity mechanics)

The campaign runners (`tools/h1_protocol.py`, `tools/campaign.py`,
`tools/h3_campaign.py`) exist so that "an agent improved the system" is
a measurement, not an anecdote:

- **Pinned worktree.** The research agent works in a worktree pinned to
  a recorded commit. Replication arms must run from a commit that
  predates any committed analysis of the same experiment — an early
  codex arm read the claude findings from its own worktree and
  "replicated" them; that run is kept, labeled contaminated, as the
  cautionary tale (`analysis/h2/h2_findings.md`).
- **Separate contract.** Research sessions run under
  `harness/CLAUDE.research.md` (goal, budgets, no-cheating rules, idea
  logging) — not the development CLAUDE.md.
- **Budgets enforced from outside.** Token ceilings are counted from
  the live API stream by the runner (new-tokens semantics: input +
  cache-creation + output, cache reads excluded); wall ceilings
  likewise. The harness enforces the frozen set and the idea gate; the
  agent cannot opt out.
- **Held-out scoring.** Dev seeds (0..49) are the agent's; seeds
  100..107 are scored by the runner after the session ends, with the
  library/graph as the agent left it. Dev-set numbers never headline.
- **Everything recorded.** Per-scenario records carry stop reason,
  token/wall spend, rollout trajectory, holdout result with failure
  taxonomy, frozen-set audit, and skill-library state.

## Reading results

Start with the findings file in each `analysis/` subdir — they are
written to be quotable, including their own caveats and the exact
commits/seeds/tiers per row. The rule this repo enforces on itself:
never aggregate runs across different commits, seeds, or tiers into one
number; when a cell is partial (timeout, infra abort) say so in the
cell, not a footnote.
