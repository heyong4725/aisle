# Phase 2 + Phase 3 completion report

(Design doc §8.3/§8.4; compiled 2026-08-15 at the close of the
autonomous build/measure loop. Every claim links a mainline artifact;
caveats are stated where the measurement earned them.)

## Phase 2 DoD (§8.3) — complete

| DoD item | artifact | headline |
|---|---|---|
| pass@1/pass@8-over-wallclock, T1/T2 | analysis/a1, analysis/t2/t2_curve_findings.md | T1 expert 1.0 per rung; T2 expert 0.08 (the deliberate perception wall) |
| verifier fidelity | analysis/ver6-fidelity | realistic-vs-oracle agreement measured; live sidecar agrees with offline replay |
| iteration latency (H4) | analysis/h4 | idea→credited-result latency, relaunch vs hot-swap |
| A1 (agent vs expert graph) | analysis/a1 | composition measured against the hand-written baseline |
| A3 (params-only vs params+code) | analysis/a3/a3_findings.md | params-only matched full authorship at HALF the tokens on T1 — schema-as-subsidy where the registry covers the task (n=1, easiest tier) |
| A7 (realistic verifier drives the loop) | analysis/a1, VER-14 sidecar work | verifier-driven loop measured; budgets re-derived (#177 era) |
| zero unclamped guard violations | every campaign record | holds across ~40 agent sessions |
| post-mortems | analysis/postmortems | per-student "strangest thing" entries |

## Phase 3 DoD (§8.4) — CLOSED 2026-08-16: five of six met, one NOT MET

| DoD item | artifact | headline |
|---|---|---|
| H3 plot (ASPIRE ablation) | analysis/h3/desk (+ retail analysis/h3) | desk T1→T4 two-arm campaign: verdict UNDECIDED under strict admissibility; no speedup measured — the ladder's difficulty spacing (T1/T4 easy for both arms, T2/T3 beyond both) is the finding; skill-reuse mechanism verified live |
| fleet-scaling plots (A5) | analysis/a5 (findings + SVG) | throughput 1.6/4.1/4.3 succ/hr at N=1/4/8 — knee at ~4 lanes/host; token super-linearity +22%/+31%; quality contention-invariant |
| agent-comparison table (A4) | analysis/a4/a4_findings.md | both agents solve T1 at 1.0/1.0; Claude ~2× cheaper end-to-end; Codex faster to first success then over-iterates (n=1/arm) |
| cross-embodiment table | SO-101 profile, M0-5 gate, ADR-27 lineage | profile swap ≥0.80; variant nodes documented |
| skill library ≥5 evalcarded | registry + skills/ | **NOT MET — CLOSED 2026-08-16 at 3** (s1-driver-v2, s3-driver-v1, ik-transfer-v2). The library reached its ceiling: source for the recovered three is provenance-verified (#252), the motion-class `ik-transfer-v2` was reviewed and human-merged (#258, evalcard 1.0), and ADR-37's floor refuses t2-scan-pose at 0.33 and t2-scan-tsm at 0.0 on their own evalcards. See "Closing the skill-library row" below |
| agent PRs reviewed with written notes | analysis/reports/agent_pr_review_notes.md | **DONE** — owner-signed 2026-08-15 (#242). **All three** of the first pass's flags were harness findings, not agent misbehaviour (#243 eval floor fixed; #244 detector import dropped as misattributed, and #253 showed the alleged import was never in the file; #245 retention). Review was possible for **2 of 5** skills |

## The two headline claims, as measured

- **H5 (structural safety): unbroken.** Wrong-medicine deliveries: 0 —
  every tier, fleet size, agent CLI, and campaign arm, under free
  agent-authored motion code throughout.
- **H4/substrate:** supported on efficiency at T1 (A3), stress-tested by
  H3's null (the substrate did not make hard tiers easy — honest scope).

## Correction (2026-08-15, amended 2026-08-16)

As first published, the skill-library row read "5" and named three skills
that could not be produced for review. The row is the one place this report
overclaimed, and the mechanism is worth stating plainly because it is a
finding in its own right: campaign worktrees live under gitignored `runs/`
with no archival step, so `skills_after` in a campaign record can name a
skill whose source is nowhere to be found.

The original wording said the three were "gone", on a search of all 49
branches, the operator filesystem, and 51 dangling git objects. **That was
wrong, and the way it was wrong is instructive.** The search was exhaustive
for the reviewing checkout and blind to everything else; the sources were
alive the whole time in campaign worktrees on the machine where the
campaigns actually ran. #252 staged them and the origin is **VERIFIED**
(2026-08-16, on the campaign machine): paths present, file mtimes
`Aug 14 10:08` and `Aug 14 19:22`–`19:24` — during the L/T2-r2 and A3
arm-F sessions that authored them — and every staged file diff-verified
byte-identical against its worktree source.

The lesson is not "search harder". A campaign record (`skills_after`) named
artifacts with no pointer to where they lived, so *any* reviewer outside
that machine would have concluded the same thing. That is what #245 fixed
(`refs/campaign/<name>` puts the pointer in a shared object store) and what
#247 carries forward. The three skills were retrievable; the protocol just
never told a reviewer where to look.

`analysis/h3/desk/desk_analysis.json` still names them in `skills_after`,
correctly: they WERE registered and they DID ride into later tiers, so the
H3 reuse mechanism claim stands. What does not stand is counting them toward
a library DoD that presumes a reviewable, mergeable artifact (§9.4).

#245 fixes the discoverability hole for future campaigns
(`refs/campaign/<name>` puts the pointer where any reviewer can follow it).
These three were recovered by hand from the campaign machine instead (#252)
— which is the manual version of what #245 now does automatically, and not
a route a reviewer on another machine could have taken.

## Closing the skill-library row (2026-08-16)

**Closed NOT MET at 3** (amended 2026-08-16 after #258). Recorded as a
decision rather than left pending, because the outcome never depended on
anything still open.

The library holds `s1-driver-v2`, `s3-driver-v1`, and — since the
owner-merged registration in #258 — **`ik-transfer-v2`**, the recovered
motion-class trajectory skill, evalcard **1.0** on the `t1-l1-routed-transfer`
suite. All three are evalcarded and human-merged. The DoD asks for ≥5.

The recovery (#252, provenance verified on the campaign machine) changed the
count, not the verdict, and it has now been taken to its ceiling of 3:

| candidate | class | evalcard | under ADR-37's floor |
|---|---|---|---|
| `t2-scan-pose` | perception | 0.33 | refused (declares 0.0, measures 0.33) |
| `t2-scan-tsm` | decision | 0.0 | refused twice over |
| `ik-transfer-v2` | **motion** | **1.0** (min 0.75) | cleared — **registered, #258** |

All three eval graphs are accounted for — `ik-transfer-v2`'s is now
`graphs/eval_ik_transfer_v2.yaml`, frozen with its ADR-30 turn plan under
ADR-36 (`env_hash` 73 → **75 files**); `t2-scan-tsm`'s is staged with its
source; `t2-scan-pose`'s eval points at `graphs/agent_campaign.yaml`. So all
three are re-evaluable and the §8.4 reviews can be done properly.

**`ik-transfer-v2` is the §9.4 trust-tier case completing end to end**, and
it is worth stating as such: an agent authored a `safety_class: motion` node
in response to a trace-cited collision, shipped it with an eval suite and a
regression population, measured 1.0, had it lost to a retention gap, had it
recovered and provenance-verified, reviewed, and then human-merged into the
registry. That is the full governance path §9.4 specifies, exercised once,
on the class that matters most.

**The open work this leaves** is a real merge question rather than a DoD
one: `ik-transfer-v2` is motion-class with a measured 1.0, the trust-tier
case §9.4 exists for, and it is now reviewable. Registering it is a
governance decision for the owner, not a Phase-3 deliverable.

**What the number means, and what it does not.** It is not evidence that
agents failed to produce reusable skills — they produced five, and the H3
campaign shows real cross-suite reuse (`s3-driver-v1` appears verbatim in a
desk deliverable). Two things kept the library small, and neither is about
agent capability:

1. **Discoverability, not loss.** Three skills were authored inside campaign
   worktrees the protocol never archived (#245). They survived on the
   campaign machine and were recovered (#252) — but no committed record
   pointed at them, so the first-pass §8.4 review could only cover **2 of
   5**. That cost a review cycle and produced a false "gone" verdict in this
   report; it is not why the library is small.
2. **The tiers — this is why.** T2 and T3 remain unsolved at session
   budgets, and the skills authored against them measured 0.33 and 0.0.
   They are genuinely unproven, and ADR-37's floor refusing them is the
   floor doing its job. A library of 5 would have required solving T2,
   which no arm did.

Read together with the H3 verdict (UNDECIDED, no measured speedup) the
result is consistent: **the desk ladder's difficulty spacing, not the
library mechanism, is what this phase measured.** The mechanism works; there
were not enough qualifying skills for it to be exercised at the size the DoD
anticipated.

## What the governance review changed (2026-08-15)

Running the §8.4 review to completion turned up three harness defects,
all now closed, and none of them agent misbehaviour:

- **#243** the skill eval floor was self-graded — `min_pass_rate` came
  from the candidate's own `eval.yaml`, so a skill shipping 0.0
  registered at 0.0 and the gate reported ok. `t2-scan-tsm` is the live
  case. Fixed: `REGISTRY_MIN_PASS_RATE` (ADR-37).
- **#245** campaign deliverables were never archived, so three
  agent-authored skills ceased to exist. Fixed forward:
  `refs/campaign/<name>`; recovers nothing.
- **#248** at rung L2 the policy and the realistic judge would share the
  detector backbone. No reported number is affected — every VER-6
  measurement ran at L0, where the policy calls no detector — but the
  next L2 measurement would have read as an independence claim it could
  not support. `harness fidelity` now labels every report.

**#244** (a validator ban on policy nodes importing the judge's
detector) was dropped: the curated core does the same thing, so the rule
would have rejected two frozen expert graphs. The flagged skill copied
the house style.

The pattern is worth stating for the governance paper: an agent-code
review that ends up indicting the review machinery three times, and the
agents zero times, is evidence about where the risk in this loop
actually sits.

## Known-open ledger (for the next session)

Analyzer follow-ups: wipe_leak pin-tracked-skill semantics; W/T2
treatment_drift derivation trace; T2-class holdout scoring windows;
campaign_metrics ancestry rule for treatment-commit arms (A3 arm-P
artifact). Infra: sim-lockstep into the rollout path (fleet fidelity);
codex mid-session token-counter drift. Science: T2/T3 remain unsolved
by any arm at session budgets — the standing challenge the tiers were
built to pose. Governance: the eval's SEED SET and episode count are
still candidate-chosen (ADR-37) — the same self-grading shape #243
closed, one field over.
