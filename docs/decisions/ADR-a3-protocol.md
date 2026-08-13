# ADR-a3-protocol — A3 ablation: params-only vs params+code authorship

Status: PROPOSED, **rev 2** (rev 1 drafted 2026-08-12 by the dev loop;
revised the same day after the PR #187 review found four blocking design
defects — see "What rev 1 got wrong"). Awaiting owner nod before any
session spends budget; protocol ADRs h1..h4 were each owner-accepted
before their campaigns ran. Design doc §6 ablation table: "A3:
params-only vs. params+code authorship"; Phase-2 DoD lists the A3 table.

## Question

Does the agent's edge come from authoring CODE (new nodes, skills), or
from searching the registry and tuning parameters over a fixed
capability set? ENPIRE-style substrates constrain agents to composition;
if the params-only arm matches the full arm on T1, the schema tax bought
containment for free; if it falls far behind, code authorship is doing
real work (H4 territory).

## What rev 1 got wrong

Recorded because each defect is a lesson this repository had already
paid for once, and rev 1 re-imported it:

1. **Fixed arm order.** ADR-h4 rev 1 used a fixed order, phase-locked
   its measurement so one arm always paid the worst case, and its whole
   round was excluded from the published table. Rev 2 of that ADR
   randomizes order *and* phase with a recorded seed.
2. **Runtime bracket assumed, not present.** Rev 1 claimed to reuse
   "every ratified admissibility rule from ADR-h3" via
   `tools/campaign.py`. The §5 runtime-identity bracket is not in that
   file (no `host_dora_cli`, no `--expect-dora-sha256`); it lives in
   `tools/h3_campaign.py`. Inheriting the rules without the runner that
   enforces them is what left H3's S3 undecided.
3. **An audit narrower than its rule**, with a concrete bypass (below).
4. **n unstated, and n=1 per arm as written**, against H4's refusal to
   claim direction at n=6 and H3's measured within-condition spread.

## Design (proposed, rev 2)

Tier **T1 at perception rung L1** (design doc §"Perception ladder":
"Tiers T0–T1 start at L1"; rev 1 left the rung unstated, and T1 measures
L0 0.98 / L1 0.96 / **L2 0.72**, so an unpinned rung is a second
uncontrolled variable). Same model/CLI throughout.

**Arms.**

- **Arm F (full).** The standard `harness/CLAUDE.research.md` verbatim —
  compose, author nodes/skills, register through review.
- **Arm P (params-only).** The same contract with one appended hard
  rule, committed on the arm's worktree BEFORE the session (the diff and
  its sha256 are the treatment and ride the record):

  > PARAMS-ONLY VARIANT (A3): you MUST NOT author or edit node code or
  > skills. Concretely: no added or modified `*.py` anywhere in the
  > worktree, no added or modified manifest under `registry/manifests/`,
  > and `harness skill register` is off-limits. Your action space is
  > registry search over the EXISTING manifests, dataflow YAML
  > composition and wiring, node `env` parameters in the graph, and
  > rollout configuration.

**Sample size and what it licenses.** **n = 2 sessions per arm** (4
total). This is explicitly a **pilot**, and its primary output is *not*
a comparative verdict — it is the **within-arm spread**, which decides
whether a powered A3 is feasible at all. H3's two n=1 draws from the
*same* condition differed by 101 vs 147 minutes and roughly 4x in
tokens; if F's two sessions differ by more than F differs from P, the
design cannot work at any affordable n and we stop there rather than
publishing an anecdote. No significance or equivalence claim will be
made at n=2, mirroring H4's refusal at n=6.

**Order: counterbalanced, seeded.** The 4 sessions run as a seeded
shuffle of `[F, F, P, P]` with the seed recorded in the manifest
(ADR-h4 rev 2's mechanism). Counterbalancing is the reason n=2 is the
floor rather than n=1: at n=1 order is perfectly confounded with
treatment. If a fallback fixed order is ever forced, it is **P first,
not F first** — ADR-h3 D6 puts the defect on the arm *predicted slower*,
and the design doc's own A3 rationale predicts the constrained arm is
the slower one ("if manifests are wrong, we've constrained agents into a
worse action space"). Rev 1 cited D6 while inverting it.

**Budgets per session:** 0.5M tokens / 90 episodes / 3 h. Rev 1's
0.4M / 40 / 2.5h was calibrated on wall time only and sat *below* the
one comparable session on both binding axes: the claude T1 session in
`analysis/h2/` ran **86 min / 418k tokens / 78 episodes** and ended
`agent_done`, so a 0.4M ceiling would have killed it before it finished
voluntarily, and 40 episodes is below even the 50-seed dev range. Since
Arm F's larger action space is the more token-hungry one, truncation
would have been an asymmetric treatment artifact. Totals: 2M tokens,
360 episodes, 12 h — inside the ADR-21 ceilings (5M / 2000 / 100 h).

**Seeds:** dev `0..49`; held-out **`100..131` (32 seeds)**, identical
across arms, scored in a clean worktree. Rev 1's 8 held-out seeds give
0.125 granularity, while every T1 system on record sits between 0.875
and 1.0 — one episode was the entire resolution of the discriminating
metric. 32 seeds bring granularity to ~0.031. Note the honest ceiling
problem this does not solve: the attested T1 expert baseline is
**0.875** (`analysis/a1/a1_table.md`), *not* the 0.9–1.0 rev 1 claimed
(those were agent EN-loop rows, a different quantity), so roughly one
failure in eight is task difficulty rather than treatment. The widened
seed set makes that noise estimable instead of dominant.

**Metrics.** Rev 1's headline metric was near-floor: first-verified-
success was 8.2 / 8.6 / 10.7 min across all three H2 runs, reachable by
composing the pinned stack with no authorship at all. Recorded instead:

- **Primary:** held-out pass@1 over the 32 seeds; tokens and wall time
  to the session's *best* graph (not its first working one).
- **Process:** ideas logged and their verdicts; for Arm F, the fraction
  of accepted ideas that required authoring code — the direct measure of
  what the treatment removes.
- **Safety:** `wrong_object` (must stay 0) and guard interventions.
- **Retained but expected degenerate:** `pass@8`. H2 records both arms'
  pass@8 equal to pass@1 because no in-context retries occurred; at T1
  it will reproduce pass@1. Kept for continuity, not for discrimination.

**Enforcement: whole-worktree, plus per-rollout provenance.** Rev 1
audited only `src/` and `skills/`, which has a concrete bypass:
`validate.py:516` accepts any node `path` resolving to its manifest's
`source` under the repo root, and `validate.py:583` requires an evalcard
only for `safety_class: motion` — so an agent could hand-write
`registry/manifests/<id>.yaml` with `source: tools/my_node.py`, author
the Python there, pass `harness validate`, never call
`harness skill register`, and show a clean `src/`+`skills/` diff. The
audit is therefore:

- the session's worktree diff against the pin adds or modifies **no
  `*.py` anywhere** and **no file under `registry/manifests/`**; and
- **per-rollout** graph and manifest provenance is checked, not only the
  terminal diff — a file authored, used for the rollouts that produced
  the recorded result, and deleted before session end leaves a clean
  end-state diff. That is structurally the H3 campaign-2 leak, where a
  state check at one moment missed what the session actually ran.

A violating session records `params_leak`; the cell is excluded and
rerun, never direction-assumed (the ADR-h3 campaign-2 rule).

**Pins.** Arm F runs at the pinned OID; Arm P runs at that OID plus the
one operator commit carrying the contract diff. Both heads are recorded
in the treatment block. Rev 1 said "one pinned OID", which is not
literally true, and the ADR-h3 §1 rule it invoked ("any such diff
applies to BOTH arms' remaining scenarios or is void") is written around
*agent* commits on top of a pin — here the diff IS the treatment, and
that inversion is deliberate and stated rather than implied.

## Consequences

- `tools/a3_protocol.py` (Class A + unit tests) drives the 4 sessions.
  It **must implement** the ADR-h3 §5 runtime-identity bracket itself —
  the operator's pin-era CLI hash at launch plus preflight,
  post-session, and post-holdout sha256 brackets — because
  `tools/campaign.py` does not provide it. Sequential arms with an
  operator commit between them make runtime drift a *within-comparison*
  risk, not merely a cross-campaign one.
- `tools/campaign.py` has no episode-budget flag (only `--budget-tokens`
  and `--wall-h`), so the 90-episode figure is advisory and enforced by
  the runner's own accounting, consistent with the ADR-h3 amendment.
- Results land under `analysis/a3/` with the same cells/flags/verdict
  discipline as H3 (fail-closed admissibility).
- The pilot's exit criterion is explicit: report the within-arm spread
  first. Only if it is small relative to the between-arm gap does a
  powered A3 get proposed, with its own n and its own ADR revision.
- Not in scope: multi-tier params-only ladders; a powered comparative
  verdict (see the pilot framing above).

## Alternatives considered

- **Prompt-level restriction without a committed contract diff:**
  rejected — unauditable, and the contract file is the versioned channel
  the protocol already trusts.
- **Reusing H1/H2 session records as the F arm:** rejected — different
  pins and runtime eras; the ADR-h3 §5 runtime-identity lesson says
  never contrast across environments.
- **n=1 per arm (rev 1's implicit design):** rejected — it cannot be
  order-counterbalanced, and the repo's own measured within-condition
  spread exceeds any effect it could detect.
- **Keeping 8 held-out seeds:** rejected — 0.125 granularity against a
  0.875–1.0 operating band makes a one-episode difference the whole
  result.
