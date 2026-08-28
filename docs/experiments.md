# Experiments: hypotheses, status, and where results live

Status as of 2026-08-28 (commit `93de5e0`). **Phase 2 and Phase 3 are both
closed** — see [`../analysis/reports/phase2_phase3_report.md`](../analysis/reports/phase2_phase3_report.md)
for the DoD-by-DoD record. Design:
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

The robot task is the instrument. The primary experimental object is an
autonomous engineering system:

```text
coding agent + contract + tools + registry + mutable robot artifacts
             + dora runtime + trusted envelope + campaign budget
```

The agent acts between episodes. It composes a typed graph, changes nodes or
parameters, reads trace and failure evidence, and may register an evaluated
skill. The resulting graph acts within an episode. Separating those loops lets
us measure two different questions: whether the coding agent can improve a
robot system, and whether the chosen inner-loop architecture—classical, VLA,
world-model-based, WAM, or hybrid—performs well.

The H1-H5 campaign family isolates five claims:

| Claim family | Named treatment or contrast | Outcomes needed for a verdict |
|---|---|---|
| Build | agent + registry composition under a frozen task | validation, launch, repair cycles, first task success |
| Diagnose and improve | trace-guided research loop under a fixed budget | held-out performance and time/token/rollout cost |
| Reuse | persistent evaluated library vs. wiped arm | matched later-task time-to-success and successful reuse |
| Substrate | typed dataflow/hot-swap workflow vs. matched alternative | iteration latency, failure rate, change attribution |
| Safety | free agent iteration behind fixed trust boundaries | adverse outcomes, unsafe proposals, guard interventions and bypass rejections |

Learned models add treatments inside this design; they do not replace it. A
model family name is insufficient evidence. A valid comparison also records
the checkpoint/revision, preprocessing, precision, inference backend and
device, observation and action contract, decoding parameters, latency, compute
cost, and any model-specific randomness.

## Evidence and claim discipline

Evidence collection exists to make the causal chain inspectable:

```text
idea -> frozen treatment -> validated graph -> seeded execution
     -> outcomes/traces/costs -> integrity audit -> scoped claim
```

The chain is deliberately fail-closed. A run with unknown code, treatment
drift, incomplete held-out scoring, or a failed post-run audit may still be
useful diagnostic material, but it cannot silently become verdict evidence.
Likewise, many episodes from one graph estimate that graph's task performance;
they are not independent replications of a research agent's ability to discover
the graph. Independent agent sessions are the research-process replicates.

## Hypotheses

| Id | Claim | Status |
|---|---|---|
| H1 | Zero-shot graph composition ≥80% | **Measured, not met** — 40/40 schema-valid, but 15% (claude) / 65% (codex) launch zero-shot; the gap is one mechanism: manifests pointing at uninstalled hub packages. Validator now surfaces `INSTALL_MISSING`. → `analysis/h1/` |
| H2 | EN-loop iteration to ≥90% pass@1 | Claude arm **met** held-out (1.0); codex arm 0.875 held-out at N=8 (one `dropped`), with dev-side evidence of a ≥0.9 system (30-episode dev run at 0.967). Both at commit `e8f163ab`. → `analysis/h2/` for the full verdict |
| H3 | Persistent skill library ≥2x faster on later tasks | **UNDECIDED on both suites; no speedup measured.** *Retail (S1→S3):* `met: null`, `complete: false` — every library-arm cell fell to drift (repo, treatment or runtime) under the ADR-h3 admissibility amendment; the earlier "NOT MET" headline was dissolved by the retroactive `treatment_drift` flag on L/S2, not replaced by a met verdict. *Desk (T1→T4, the §8.4.2 ASPIRE ladder):* `met: null` under strict admissibility, 13 record-derived caveats. Interpretable direction with that caveat — T4 ratio **~1.03** (L 894 s vs W 872 s), parity rather than ≤0.5; T2/T3 unsolved by either arm. **The measured finding is the ladder's difficulty spacing, not the library**: T1/T4 too easy (no headroom), T2/T3 beyond both (no success to accelerate). Reuse itself was verified live — L/T3-r2's deliverable embeds `s3-driver-v1` verbatim, a retail→desk cross-suite transfer. Post-close follow-up (T2-only differential, #306): both arms 0.25 holdout, library arm **35% cheaper** with verified reuse — accumulation measured as economy, not ceiling. → `analysis/h3/h3_findings.md`, `analysis/h3/desk/desk_findings.md`, `analysis/h3/t2_differential/findings.md` |
| H4 | Hot-swap iteration beats relaunch | **Measured at T0**, phase-randomized (ADR-h4 rev 2): hot-swap median 32.4 s vs relaunch 41.8 s (ratio 1.29), n=6 per path, zero infra failures; the mutation mechanism alone is ~1.7x faster (2.4 s vs 4.0–4.7 s). Extremes overlap and no significance/equivalence claim is made at n=6. **UNATTESTED** dev measurement (ADR-24): no reproducibility claim. Two superseded designs are retained as evidence. → `analysis/h4/h4_findings.md` |
| H5 | wrong-object stays 0 under free iteration | **Holding, on a much larger denominator.** 0 wrong-object in 224/224 episodes across the three H2 runs (`analysis/h2/`), and 0 in every campaign since: desk-H3 both arms, A3's two arms, A4's two agent CLIs, A6's two reset arms, and **all 13 A5 fleet lanes under 8-way concurrent agent-authored iteration**. ~45 agent sessions have authored or driven motion freely without one wrong-medicine delivery — the post-close additions include every H6 operation cell, every M1 VLA-driven episode, and every T2/T4 re-measure. Inadmissible H3 cells still do not contribute to the verdict denominator — a cell that cannot support a performance claim cannot support a safety one either — but the admissible campaigns above do. |
| H6 | Agent operates a running system: detect, localize, repair, recover | **SUPPORTED, 3/3 pre-registered cells** (2026-08-26; ADR-h6-operation-protocol + five measured amendments). One induced fault per safety tier (perception +45 mm pose bias / decision +60 mm grasp lift / motion executor stall — magnitudes preflight-measured after the expert absorbed the originals); the operator agent detected each from live evidence alone (299–447 s), localized the correct node with cited, audited evidence, and restored 1.0 via validated relaunch (the campaign also measured that hot-swap kills lockstep dataflows — the repair path is relaunch until turn-aware swap lands). Zero `wrong_object`, zero guard bypass. n=1 per fault class. → `analysis/h6/findings.md` |

## Ablations (design doc §6)

All six that have run are n-limited single matched pairs or single sweeps.
They are directional, and each findings file states its own bound.

| Id | Contrast | Result |
|---|---|---|
| A1 | agent-composed vs expert graph | End-to-end estimand: compose, launch, pass — a composition that never launches scores 0. An earlier draft conditioned on the 16/40 graphs that launched, which selects away the dominant failure mode A1 exists to measure; corrected in PR #70 review. Provenance-explicit inventory at `analysis/a1/a1_table.md` — **read its "what the records do NOT support" section before quoting numbers.** The attested T1 rerun is the cell of record and replicates the unattested run exactly (0.875, one `dropped`) |
| A3 | params-only vs params+code | **The constrained arm won on efficiency at equal quality** (pin `8af9b47a`, runner #234, treatment diff sha256 recorded). Arm P: first success 9.8 min, 200k tokens (50% of budget), 24 min wall, 1 dev rollout. Arm F: 13.8 min, 396k (99%), 85 min, 4 rollouts. Both 1.0/1.0 held out, 0 `wrong_object`; arm P's params-leak audit clean. Reading: **schema-as-subsidy** — where the registry already covers the task, denying code authorship costs nothing and halves the spend. n=1 per arm, easiest tier. → `analysis/a3/` |
| A4 | Claude Code vs Codex | **Both solve T1 outright**: 1.0/1.0 held out, 0 `wrong_object`, identical budgets (0.4M / 2.5 h), prompt, seeds and pin (`cb814e12`). Codex first success 8.1 min then kept iterating (5 rollouts, 364k tokens, 73 min); Claude converged in 2 rollouts and stopped (9.7 min, 186k, 36 min). Style, not capability — at equal quality Claude's session was ~2× cheaper end-to-end. **n=1/arm at one budget on the easiest tier: a lower-bound comparison, reported as such.** Kimi Code out of scope v1 (no CLI login). → `analysis/a4/` |
| A5 | 1 vs 4 vs 8 agents (fleet scaling) | **Throughput saturates at ~4 lanes/host**: 1.6 → 4.1 → 4.3 successes/hour. 4→8 bought +5% throughput for 2× agents and 2.2× tokens. Median first success 10.5 → 14.1 → 18.0 min (graceful). **Quality is contention-invariant** — holdout 1.0 on every lane, including one fleet-8 lane that never logged a dev-seed success yet scored 1.0 held out (the deliverable-quality-vs-dev-luck split also seen in desk-H3 L/T2). Token super-linearity +22%/+31% per agent, matching ENPIRE's direction. 0 `wrong_object` in 13 lanes. **Protocol deviation recorded in the ADR:** lanes share the host with their own sim rather than one batched bridge; peer cross-pollination deferred. → `analysis/a5/` |
| A6 | teleport vs behavioral reset | **What teleporting hides, quantified.** Paired 10-episode T1 arms, seeds 0..9, idle machine: teleport 1.00 pass@1 / 6.4 min; behavioral 0.80 (2 `never_grasped`, seeds 5 and 9) / 9.6 min, **+19 s per episode**. Reset outcomes: 7 behavioral success, 3 audited `fallback: true`. The reset is itself a manipulation task that fails sometimes — ENPIRE's "reset is often easier than the task", with *often* doing real work. Idea I16 closed `flat`, 3/4 pre-registered expectations met; **the miss is the measurement's point**. 0 `wrong_object` both arms. → `analysis/a6/` |
| A7 | realistic verifier drives the loop | Verifier-driven loop measured, budgets re-derived. Depends on the VER-6 fidelity number, which currently rests on a **rung-L0** measurement — see the backbone caveat below. |

### The A7 / VER-6 backbone caveat (#248)

Verifier fidelity is only evidence of portability when the realistic judge is
an *independent* estimate of the same event. At perception rung **L2** it would
not be: the policy path (`l2_pose`, `label_reader`) and `verifier/realistic.py`
both call `models.load_pinned("identity")`, so their errors correlate — an
episode the shared detector misreads is one where the policy acts wrongly *and*
the judge fails to notice, cancelling into agreement rather than surfacing as
disagreement.

**No reported number is affected.** Every VER-6 measurement to date ran on
`expert_t0.yaml`, which declares no rung and is therefore L0, where the policy
consumes ground-truth poses and calls no detector. The trap is prospective and
springs the wrong way: an L2 fidelity run yields a *better* agreement than L0
and reads as the pipeline improving on a harder rung. `harness fidelity` now
labels every report with a `backbone` verdict, persisted beside the rates and
failing closed on an unresolved rung.

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
