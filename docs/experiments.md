# Experiments: hypotheses, status, and where results live

Status as of 2026-08-10 (commit `0a19154`). Design:
`Project_AISLE_Experiment_Design.md` §6 (hypotheses/metrics/ablations)
and §11.5 (retail transfer). Committed findings live in `analysis/`;
raw run data in `runs/` (gitignored — findings files copy in what the
claims need).

The canonical status table is [the README's](../README.md#status) (issue
#142). This page carries protocol and evidence detail; if a verdict here
ever disagrees with the README, the README is right and this page is stale.

For the technical framing behind these protocols—what the research object is,
why evidence collection is part of the system, what claims each record can
support, and how VLA/world-model/WAM comparisons fit—read the
[AISLE research program](research-program.md).

## What is actually under experiment?

The robot task is the instrument; the experimental object is the autonomous
engineering system around it (coding agent + contract + tools + registry +
mutable robot artifacts + dora runtime + trusted envelope + campaign budget).
That framing — outer loop vs. inner loop, the five claim families, why a model
family name is insufficient evidence, and the evidence/claim discipline — is
developed once in the [AISLE research program](research-program.md#1-the-research-object).
It is deliberately **not** restated here: this page owns protocol and evidence
detail, and a second copy of the framing is exactly the drift issue #142 was
opened to stop.

Two consequences that bear directly on the protocols below:

- Many episodes from one graph estimate *that graph's* task performance. They
  are not independent replications of a research agent's ability to discover
  the graph — independent agent sessions are the research-process replicates.
- A run with unknown code, treatment drift, incomplete held-out scoring, or a
  failed post-run audit can be useful diagnostic material, but it cannot
  silently become verdict evidence. The chain is fail-closed by design.

## Hypotheses

| Id | Claim | Status |
|---|---|---|
| H1 | Zero-shot graph composition ≥80% | **Measured, not met** — 40/40 schema-valid, but 15% (claude) / 65% (codex) launch zero-shot; the gap is one mechanism: manifests pointing at uninstalled hub packages. Validator now surfaces `INSTALL_MISSING`. → `analysis/h1/` |
| H2 | EN-loop iteration to ≥90% pass@1 | Claude arm **met** held-out (1.0); codex arm 0.875 held-out at N=8 (one `dropped`), with dev-side evidence of a ≥0.9 system (30-episode dev run at 0.967). Both at commit `e8f163ab`. → `analysis/h2/` for the full verdict |
| H3 | Persistent skill library ≥2x faster on later tasks | **Verdict PENDING** (`met: null`, `complete: false`) — campaign ran; both tiers UNDECIDED. Every library-arm cell fell to drift (repo, treatment or runtime) under the ADR-h3 admissibility amendment, and the wiped arm's clean cells never succeeded at S2/S3, so one arm cannot decide a ratio tier. The earlier "NOT MET" headline was dissolved by the retroactive `treatment_drift` flag on L/S2, not replaced by a met verdict. → `analysis/h3/h3_findings.md` |
| H4 | Hot-swap iteration beats relaunch | **Measured at T0**, phase-randomized (ADR-h4 rev 2): hot-swap median 32.4 s vs relaunch 41.8 s (ratio 1.29), n=6 per path, zero infra failures; the mutation mechanism alone is ~1.7x faster (2.4 s vs 4.0–4.7 s). Extremes overlap and no significance/equivalence claim is made at n=6. **UNATTESTED** dev measurement (ADR-24): no reproducibility claim. Two superseded designs are retained as evidence. → `analysis/h4/h4_findings.md` |
| H5 | wrong-object stays 0 under free iteration | **Holding** on committed evidence: 0 wrong-object in 224/224 episodes across the three H2 runs (`analysis/h2/`). H3's records do not extend the denominator: its cells are inadmissible for verdict purposes (above). |

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
