# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: verdict PENDING** (`met: null`, `complete: false`) — computed
by `tools/h3_analysis.py` from clean cells only, under the
owner-ratified provenance semantics of 2026-08-05 (PR #90). The two
verdict tiers stand:

- **S2: UNDECIDED.** No admissible L cell exists — full provenance
  resolution retroactively flags L/S2 with `treatment_drift` (the July
  campaign branch carried post-pin origin/main commits: the
  mid-campaign #58 wall-clamp fix and neighbours). The clean W cell
  (W/S2-r2) produced no deliverable (a scored 0.0). One arm cannot
  decide a ratio tier.
- **S3: TRUE (accumulation signal, n=1).** The protocol-compliant
  attempt-3 library cell is CLEAN and reached a dev success at
  **29.9 min / 168k tokens** (re-derived metric, below), while the
  clean wiped cell (W/S3-r2) burned 751k tokens without ever
  succeeding. ADR-h3 §7: only-L-succeeded satisfies the tier.

The prior "NOT MET" headline (PR #76) rested on L/S2 being clean; the
retroactive flag dissolves that basis, and no tier now decides False.
The honest claim: **one admissible S3 contrast shows the library arm
succeeding where the wiped arm never did; S2 is provenance-dead; the
campaign as a whole cannot reach a formal `met` verdict without either
an owner-accepted incomplete closure or a budget-corrected new
campaign.**

Protocol: `tools/h3_campaign.py` per
`docs/decisions/ADR-h3-campaign-protocol.md` (campaign 2 + same-model
resume after a Fable 5 quota 429 + attempt-3 rerun, commit `03da7469`,
`claude-fable-5`, dev seeds 0..49, held-out 100..107). The aborted
3.4-min L/S2 telemetry is preserved separately
(`records/arm_L/S2/token_samples-aborted-429.jsonl`).

**Reproduce everything:**
`uv run python tools/h3_analysis.py --dir analysis/h3/records` — cells,
flags, verdict, and the H5 totals over explicit aggregation sets. All
flags are machine-derived from the records: `wipe_leak` from
prior_skills on the wiped arm, `residue_leak` from the aggregates'
wipes lists, `holdout_partial` from the holdout status,
`treatment_drift`/`unattested_metric`/`unattested_env` from per-rollout
provenance under the ratified ancestry+content semantics,
`provenance_missing` failing closed where provenance is neither
recorded nor resolvable, and `metric_inconsistent` failing closed where
an admissible success exists but its timing cannot be re-derived. The
bundle carries the five aggregates, per-cell `scenario.json` +
`token_samples.jsonl`, and the one cited dev-evidence file.

## The records (assembled table, all ten cells)

| Arm | Cell | Held-out pass@1 | First success (min) | Tokens@1st | Delivery fails | Placement fails | Flags |
|---|---|---|---|---|---|---|---|
| W | S1 | 0.375 | 146.7 | 165k | 0 | 0 | — |
| L | S1 | 0.500 | 100.8 | 661k | 0 | 0 | — |
| W | S2 | 0.333 (partial) | 101.4 | 716k | 0 | misplaced 1 | wipe_leak, holdout_partial, unattested_metric |
| W | S3 | 0.000 | 48.9 | 536k | 0 | wrong_slot 7 | wipe_leak, unattested_metric |
| L | S2 | 0.000 | — | — | 0 | wrong_slot 3 | treatment_drift |
| L | S3 | 0.000 | — | — | 0 | wrong_slot 3 | treatment_drift, residue_leak |
| W | S2-r2 | 0.0 (no deliverable) | — | — | 0 | 0 | — |
| W | S3-r2 | 0.000 | — | — | 0 | wrong_slot 1 | — |
| L | S3-r2 | 0.000 | 23.2 | 323k | 0 | wrong_slot 8 | treatment_drift, unattested_metric |
| L | S3-r3 | 0.000 | 29.9 (re-derived) | 168k | 0 | wrong_slot 8 | — |

Flag provenance in brief (each exclusion's story):

- **L/S3 (attempt 1), `residue_leak`** (PR #67 review): the resume ran
  the pre-guard runner and inherited L/S2's working state.
- **L/S3-r2, `treatment_drift` + `unattested_metric`** (PR #76 review):
  the operator merged post-pin origin/main (`d737aeb`) into the
  worktree before the cell's trusted rollout, and its 23-minute
  first-success came from a LOCAL unattested skill-registration eval.
  The cell was the experiment's first voluntary finish with a library —
  an inadmissible observation that attempt 3 was run to replace.
- **L/S2 and the W S2/S3 flags (retroactive, PR #90):** full manifest
  resolution shows the July campaign branch itself carried post-pin
  main commits, and W/S2 + W/S3's first-success rollouts were not
  trusted-at-pin. These flags are richer evidence about old events, not
  new events; they dissolve the S2 tier's former clean basis.

## The S3 resolution (attempt 3, 2026-08-05) — the one admissible contrast

The rerun ran at the campaign pin (worktree restored to pin + the
tier's original `prior_skills` `['s1-driver-v2']`, drift/residue
cleared to a keep-ref, zero frozen drift). The session finished
voluntarily (`agent_done`, 253,776 of 750,000 tokens), re-authored an
s3-driver from the S1 library, and reached its first trusted dev
success at **29.9 minutes / 168k tokens**. Holdout scored 0/8 (all
`wrong_slot` — seeds 100..107 sit almost entirely outside the both-L1
feasible class; the same geometry capped every S3 cell).

**The re-derived metric (PR #90 review):** the pinned runner's
superseded strict rule (`env_baseline_oid == pin`) discarded the two
trusted dev successes and recorded `first_success_wall_s: null`. The
analyzer now re-derives the metric from primary timing evidence —
earliest success-manifest mtime minus the session start, where the
session start comes from the token-sampler file (its mtime minus the
last sample's wall_s; the sampler dies with the session). The value
carries `first_success_rederived: true` in the cell and a plausibility
guard (must fall inside the session wall) fails closed as
`metric_inconsistent` if the evidence is broken.

**Admissibility under the ratified rules:** all three dev rollouts ran
at the pin (`git_sha == pin`); their trust anchors were origin/main
heads that moved mid-cell because three unrelated PRs merged during the
session — content-equal to the pin (the committed frozen hash never
changed), which the ancestry+content semantics accept and the strict
oid rule would have rejected. Attestation is judged by the PIN's
protocol (owner-ratified): the pin predates ADR-24, so its runner
structurally cannot emit `env_attested`; an explicit
`env_attested: false` would still fail closed. Environment caveat, the
same class the whole campaign carried: the host dora CLI at attempt 3
was the post-#85 build while the pinned python API and frozen set were
identical to July's cells.

**What the contrast says and doesn't:** clean L with the S1 library
succeeded at 29.9 min / 168k tokens; clean W without it never succeeded
in 751k. That satisfies ADR-h3 §7's only-L-succeeded arm of the tier —
the first admissible accumulation signal in the campaign. It is n=1,
the wiped comparator is a different attempt generation, and holdout
transfer remained 0/8 for both arms: a signal about *reaching* S3
competence, not about *solving* S3.

## The budgets caveat (unchanged)

L/S1 needed **661k** tokens to its first success; the harder verdict
tiers got 750k (D2's split assumed accumulation would make them
cheaper). The campaign cannot distinguish "libraries don't help at S2"
from "0.75M is below the S2 entry cost" — any budget-corrected
campaign is a NEW experiment (new pin, post-ADR-24/25/26 protocol,
env-baseline pinned per issue #91).

## H5: delivery precision held on committed held-out records; placement quality did not

H5's 10x-asymmetric claim is about the wrong THING delivered
(`wrong_object` desk / `extra_item` retail, RS-7); placement-quality
failures (`wrong_slot`, `misplaced` — §11.3's family) are the lesser
claim. On committed records, per the analyzer's explicit sets:

- **Delivery-class, selected set: 0 failures in 32 executed held-out
  episodes** (`h5.selected`: highest-attempt clean cell per arm/tier —
  W/S1, L/S1, W/S2-r2, W/S3-r2, L/S3-r3; W/S2-r2 executed nothing and
  contributes zero exposure).
- **Placement-class, selected set: 9** (W/S3-r2 1 + L/S3-r3 8, all
  `wrong_slot`) — placement quality remained poor wherever S3 systems
  actually ran.
- **Historical inventory, all ten records** (`h5.all_records`,
  including flagged and superseded cells): 67 executed held-out
  episodes, 0 delivery-class, 31 placement-class (30 `wrong_slot`,
  1 `misplaced`).
- **Dev-side, committed evidence:** 2 `extra_item` in W/S1's baseline
  run (`records/arm_W/S1/dev/20260728-001009-b4d1fd.episodes.jsonl`) —
  the expert-copy baseline before the agent's fix, outside the scored
  surface.

## The one clean pair (S1) — replicated caution, not transfer

Both arms start library-empty on S1: two draws from the same
condition. L 0.5 vs W 0.375 held-out; L faster in wall time (101 vs
147 min) but ~4x the tokens to first success (661k vs 165k). n=1 per
cell — single-session variance is wide, which any eventual verdict
must weigh.

## Bundle provenance and augmentations (all disclosed)

The committed bundle reproduces the analysis standalone. Its records
carry three disclosed post-hoc augmentations, each a copy of primary
facts from the (gitignored) live tree: per-rollout provenance resolved
from the run manifests (+ `pass1` from `episodes.jsonl`), the ratified
lineage/anchor annotations (`_lineage_ok`/`_anchor_ok`, git-derived:
merge-base ancestry vs the pin; committed frozen-hash equality of the
trust anchor), and `session_start_epoch` (token-sampler evidence) for
metric re-derivation. A "no deliverable" holdout remains a structured,
fail-closed classification scored 0.0, distinct from an expired
scoring window (infra partial).

## Protocol lessons and remaining follow-ups

- **Freeze origin/main while a campaign cell runs** — attempt 3's trust
  anchors moved mid-cell (content-equal only because the frozen hash
  never changed); campaign runners should pin `--env-baseline` to the
  campaign OID (issue #91).
- Analyzer integrity is fail-closed end to end: absence of provenance,
  a half-derived annotation, an explicit failed attestation, or an
  un-re-derivable metric each exclude a cell rather than pass it.
- The formal `met` remains PENDING. Closing it is an owner decision:
  accept the incomplete ledger as the campaign's final state (S3
  signal + S2 provenance-dead), or commission a budget-corrected
  campaign at a current pin. Issue #71's determinism thread is DONE
  (ADR-25/26; the S1 pair verified layers (a)-(c) with a 27 sim-s
  bit-coherence horizon).

IDs: design doc §11.5, §6 H3/H5, §1 (10x asymmetry); ADR-h3 §7/§9 +
campaign-2, resume, and attempt-3 amendments; RS-7 (delivery class),
§11.3 (placement family); CON-5; ADR-24 (grandfather-by-pin,
owner-ratified 2026-08-05); ADR-26 (statistical outcomes).
