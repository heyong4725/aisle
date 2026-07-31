# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: COMPLETE. Formal verdict: H3 NOT MET** (`met: false`) —
computed by `tools/h3_analysis.py` from clean cells only. The S2 tier
is decided False from clean cells: neither arm's clean cell ever
reached a dev success (L/S2 burned its budget without one; the truly
wiped W/S2-r2 produced no deliverable at all). `met = all(tiers)`, so
one tier decided False fixes NOT MET regardless of the other. The S3
tier is UNRESOLVED: it has no admissible clean cell — attempt 1's
library cell is excluded for `residue_leak`, and the attempt-2 rerun
cell is excluded for `treatment_drift` + `unattested_metric` (PR #76
review; details below). The honest claim: **no accumulation benefit
demonstrated under these budgets; the S3 transfer question remains
open pending a protocol-compliant rerun.**

Protocol: `tools/h3_campaign.py` per
`docs/decisions/ADR-h3-campaign-protocol.md` (campaign 2 + same-model
resume after a Fable 5 quota 429, commit `03da7469`,
`claude-fable-5`, dev seeds 0..49, held-out 100..107). The aborted
3.4-min L/S2 telemetry is preserved separately
(`records/arm_L/S2/token_samples-aborted-429.jsonl`).

**Reproduce everything:**
`uv run python tools/h3_analysis.py --dir analysis/h3/records` — cells,
flags (all machine-derived from the records: `wipe_leak` from
prior_skills on the wiped arm, `residue_leak` from the aggregates'
wipes lists, `holdout_partial` from the holdout status,
`treatment_drift`/`unattested_metric` from per-rollout provenance),
verdict, and the H5 totals over explicit aggregation sets. The bundle
carries the four aggregates, per-cell `scenario.json` +
`token_samples.jsonl`, and the one cited dev-evidence file.

## The records (assembled table)

| Arm | Tier | Held-out pass@1 | First success (min) | Tokens@1st | Delivery fails | Placement fails | Flags |
|---|---|---|---|---|---|---|---|
| W | S1 | 0.375 | 146.7 | 165k | 0 | 0 | — |
| L | S1 | 0.500 | 100.8 | 661k | 0 | 0 | — |
| W | S2 | 0.333 (partial) | 101.4 | 716k | 0 | misplaced 1 | wipe_leak, holdout_partial |
| W | S3 | 0.000 | 48.9 | 536k | 0 | wrong_slot 7 | wipe_leak |
| L | S2 | 0.000 | — | — | 0 | wrong_slot 3 | — |
| L | S3 | 0.000 | — | — | 0 | wrong_slot 3 | residue_leak |

**L/S3's exclusion (PR #67 review):** the resume ran the pre-guard
runner (the arm-L residue guard [PR #61] merged after launch), and its
aggregate records `wipes: []` — L/S3 inherited L/S2's working state
(`graphs/agent_campaign.yaml`, unregistered skill code, debug logs),
violating the residue-clear protocol. The `residue_leak` flag is
derived from the aggregates by the analyzer, and the cell is excluded
from the verdict like every flagged cell.

## The rerun cells (attempt 2, hardened runner)

| Cell | Session | Held-out | Verdict status |
|---|---|---|---|
| W/S2-r2 | token-killed 752k @ 33 min | **0.0 (no deliverable)** | CLEAN — scored outcome (zero rollouts; nothing to run) |
| W/S3-r2 | token-killed 751k @ 92 min | 0.0 (7 missing_item, 1 wrong_slot) | CLEAN — deliverable produced, no dev success |
| L/S3-r2 | agent_done 384k @ 61 min | 0.0 (8 wrong_slot) | **EXCLUDED — `treatment_drift`, `unattested_metric`** |

**L/S3-r2's exclusion (PR #76 review).** Two protocol violations, both
now machine-derived from the record's per-rollout provenance:

- *Treatment drift:* to satisfy the ADR-24 environment gate, the
  operator merged post-pin `origin/main` (`d737aeb`, which already
  contained the published H3 findings and later harness changes) into
  the campaign worktree (`worktree_head fee9e9f`) — breaking the
  protocol's one-pinned-OID invariant before the cell's trusted
  rollout and final holdout ran.
- *Unattested metric:* the recorded 23-minute `first_success_wall_s`
  was supplied by a LOCAL, unattested skill-registration eval
  (`env_baseline: local`), not a trusted rollout at the pin.
  `campaign_metrics` now records each rollout's
  `git_sha`/`env_baseline`/`env_baseline_oid`/`env_attested` and
  derives first-success only from admissible runs.

The cell was also the experiment's only voluntary (`agent_done`,
under-budget) finish, with `s1-driver-v2` in its library — recorded
here as an **inadmissible observation only**. Whether a library
shortens S3 time-to-first-success is unresolved until a
protocol-compliant rerun replaces this cell.

The clean W/S2 rerun did not reproduce attempt 1's dev success (101
min, with the leaked skill): truly-wiped W/S2-r2 produced nothing at
all. This is consistent with the leaked state helping, but it does not
identify the cause or direction of the leak's effect — agent sampling
variance and the hardened-runner context also changed (ADR-h3
campaign-2 amendment: exclusion plus rerun, never a direction
assumption). The contrast vindicates excluding contaminated cells.

## The headline: the library arm's clean verdict-tier cell never succeeded

L/S2 — clean, residue-cleared, same model — burned its full 750k-token
budget **without completing a single dev rollout**: the held-out
scoring run is the only rollout in its record, `first_success_wall_s`
is null, held-out pass@1 is 0. A tier where arm L never succeeds is
not-met by construction (ADR-h3 §7), and `met = all(tiers)`, so the
campaign verdict is capped at NOT MET before any rerun. (The excluded
L/S3 behaved identically — 752k tokens, no dev rollout, 0.0 — but
carries the flag and is not evidence.)

Two observations that sharpen the mechanism question:

- **The weak transfer channel worked; transfer didn't.** L/S2 started
  library-empty (L/S1 registered nothing) yet RE-CREATED and registered
  `s1-driver-v2` from the read-only idea tree (D3). Library *presence*
  never became library *use*: no deliverable ever wired a prior skill
  (`skill_reuse_in_deliverable: []` in every cell).
- **The budgets were plausibly under water.** L/S1 needed **661k**
  tokens to its first success; the *harder* verdict tiers got 750k
  (D2's split assumed accumulation would make them cheaper). The
  campaign cannot distinguish "libraries don't help" from "0.75M is
  below the S2/S3 entry cost" — "NOT MET under these budgets" is the
  claim, and a budget-corrected campaign would be a new experiment.

## H5: delivery precision held on committed held-out records; placement quality did not

**Correction (PR #67 review — an earlier draft conflated these).**
H5's 10x-asymmetric claim is about the wrong THING delivered
(`wrong_object` desk / `extra_item` retail, RS-7). Placement-quality
failures (`misplaced`, `wrong_slot` — §11.3's placement family) are a
different, lesser claim. Split accordingly, on committed records only:

- **Delivery-class, selected set: 0 failures observed in 32 executed
  held-out episodes.** The selected set is the verdict's aggregation
  set (highest-attempt clean cell per arm/tier: W/S1, L/S1, L/S2,
  W/S2-r2, W/S3-r2; `h5.selected` in the analyzer output, PR #76
  review). W/S2-r2 produced no deliverable, executed nothing, and
  contributes ZERO exposure to this safety claim — its eight
  held-out episodes exist only in pass@1 space.
- **Placement-class, selected set: 4** (L/S2 3 `wrong_slot`, W/S3-r2
  1 `wrong_slot`) — placement quality was poor wherever S2/S3 systems
  actually ran.
- **Historical inventory, all nine records** (including flagged and
  superseded cells; `h5.all_records`): 59 executed held-out episodes,
  0 delivery-class, 23 placement-class (22 `wrong_slot`, 1
  `misplaced`; the excluded L/S3-r2 alone contributed 8 `wrong_slot`).
- **Dev-side, committed evidence:** 2 `extra_item` in W/S1's baseline
  run (`records/arm_W/S1/dev/20260728-001009-b4d1fd.episodes.jsonl`) —
  the expert-copy baseline before the agent's fix, on the agent's own
  seeds. A real delivery-class occurrence, outside the scored surface.
  (An earlier draft claimed a larger dev tally by double-counting
  holdout runs through the rollouts lists; only this file is committed
  dev evidence.)

## The one clean pair (S1) — replicated caution, not transfer

Both arms start library-empty on S1: two draws from the same
condition. L 0.5 vs W 0.375 held-out; L faster in wall time (101 vs
147 min) but ~4x the tokens to first success (661k vs 165k). n=1 per
cell — single-session variance is wide, which any eventual verdict
must weigh.

## Closed questions and remaining follow-ups

The formal record reproduces with
`uv run python tools/h3_analysis.py --dir analysis/h3/records`
(all nine cells, four aggregates, preserved aborted-session telemetry).
A "no deliverable" holdout is a structured, fail-closed classification
(the runner's `outcome` field or its exact legacy template, plus
ok=false, no scores, no failures, zero executed episodes) scored 0.0 —
distinct from an expired scoring window, which stays an infra partial.
The rerun cells' scenario records carry per-rollout provenance copied
verbatim from their run manifests (a disclosed post-hoc augmentation of
the committed bundle; the values are the worktrees' own manifest facts).

Remaining follow-ups (separate work, not part of this verdict): a
protocol-compliant L/S3 rerun to resolve the S3 tier (one pinned OID,
trusted rollouts only — the runner now derives the metric that way by
construction); issue #71's back-to-back attested determinism pair for
`expert_s1`; any budget-corrected accumulation campaign (a NEW
protocol decision).

IDs: design doc §11.5, §6 H3/H5, §1 (10x asymmetry); ADR-h3 §7/§9 +
campaign-2 and resume amendments; RS-7 (delivery class), §11.3
(placement family); CON-5.
