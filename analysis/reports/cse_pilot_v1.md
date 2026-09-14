# cse-causal-study pilot v1 (ADR-66, issue #347)

Registered 2026-09-12 on the simulation MacBook. Zero sessions collected. This
page records what was registered, what blocked collection, and the power inputs
that the pilot was meant to supply. Nothing here authorizes scored collection;
every gate in `cse-causal-study-v15` is untouched.

Naming: the pilot-first decision was accepted under the label "ADR-65" (#569,
#572) and renumbered to `docs/decisions/ADR-66.md` by #568, which now holds the
frontend-conformance ADR under ADR-65. The registration cites ADR-66.

## What was registered

| Artifact | Path |
|---|---|
| Declaration (`purpose: pilot`, derived from v15) | `analysis/freeze/cse-causal-study-pilot-v1/declaration.json` |
| Manifest (`registered_pending_review`, 124 artifact hashes, fresh salted seed commitment) | `analysis/freeze/cse-causal-study-pilot-v1/freeze-manifest.json` |
| SPEC 400 protocol (`campaign_phase: pilot`, 6 per arm) and its `--purpose power` validation | `protocol.json`, `protocol-validation.json` (same directory) |
| Power CLI output | `power.json` |
| Analyzer output on the retained (empty) record set | `records.json`, `analysis.json` |
| Executor refusal receipts | `executor-preflight.json` |

Hypotheses, endpoints, decision rules, exclusions, instrument set and budgets
are carried verbatim from v15. The declaration has no `superseded` field and
does not inherit the v15 seed commitment: 32 fresh seeds and a fresh salt were
drawn once from the OS CSPRNG into `~/aisle-private/freeze/cse-causal-study-pilot-v1/`
(BND-13) and are disjoint by value from the confirmatory v1 seed list. Only the
salted digest is committed. The design (6 per arm, alternating arms, one agent
system, one task stratum, no reruns, stop conditions) is recorded under
`pilot_design`; the outcome is recorded under `disposition`.

## What ran

No pilot session was launched, no simulation started, and no private seed
source was read by any run. Collection is blocked by three independent facts
about the current infrastructure, each reproduced in `executor-preflight.json`
without building anything new:

1. **Task surface.** The matched executor admits only the fixed
   `T1 / franka / oracle / teleport` development mode. Changing `tier` to T2 or
   `verifier` to realistic is refused with
   `development protocol requests an unsupported run mode`.
2. **Oracle isolation.** Both paired T1 surfaces deliver simulator
   `seg_overhead` to policy code (`segmented-pose`, `monolith-broker`) and route
   `verifier-oracle/episode_result` into the policy region. The non-oracle
   boundary validator rejects a segmentation mask on the policy path with
   `oracle state crosses the policy boundary`. ADR-66 section 2 keeps oracle
   isolation binding for pilots, so the T1 conformance surface cannot be
   relabelled as the pilot task.
3. **No eligible stratum.** The current calibration,
   `bnd-task-band-calibration-v10`, records that its short-composition candidate
   is not perception-eligible (BND-7) and the role is open pending a new
   candidate round (BND-10). There is no selected task stratum to sample.

A fourth fact bounds any future attempt: the only admitted-session launcher
that drives an actual frontend is the conformance fixture, whose provider must
be the local scripted listener. No live-provider session request builder exists
outside test code, the installed Codex is 0.154.0 against the pinned 0.153.4
profile, and the installed Claude Code is 2.1.269 against the 2.1.261 observed
in the registration.

The executor's purpose gate behaves as specified: `--purpose pilot` is refused
with `scored collection requires independent CSE-10 gate records`;
`--purpose engineering` passes that gate and fails later on the empty plan.

## Results

| Quantity | typed | monolithic |
|---|---|---|
| Sessions randomized / started / completed | 0 / 0 / 0 | 0 / 0 / 0 |
| Session success, exact Clopper-Pearson 95% interval | not computable (n = 0) | not computable (n = 0) |
| Median tokens / wall / rollouts per session | none | none |
| Validate-fix cycles per session | none | none |
| Admissibility flags | none raised (no session) | none raised (no session) |

Risk difference (descriptive): not computable. Effect direction: unobserved.
The analyzer refused the empty record set with
`records.sessions must be a non-empty list` (`analysis.json`, exit 1), which is
the correct STA-11 behaviour.

Stop conditions: no wrong-object outcome, guard bypass or unattested
environment occurred because nothing executed.

## Power (CSE-8)

`harness stats power` on the pilot protocol reports 49 sessions per arm at
80% power for the frozen smallest risk difference of 0.25, one-sided alpha 0.05,
control success 0.40. Sensitivity: 136 / 77 / 49 / 33 per arm for effects
0.15 / 0.20 / 0.25 / 0.30. **This is not a pilot-derived number.** The control
probability is the pre-registration assumption carried from v1 because the
pilot produced no control success; CSE-8 still requires the confirmatory
sample size to come from pilot-only control success. The CLI's own warning
stands: normal-approximation planning assumptions require independent review.

## Limitations (machine gates recorded in the declaration)

| Gate | Status on 2026-09-12 |
|---|---|
| CON-14 spec approval | pending, external review (not a pilot prerequisite) |
| STA-12 independent statistical review | pending, external review (not a pilot prerequisite) |
| MON-1 treatment table and expert parity (#344) | pending, no retained record |
| BND-12 task-band freeze (#346) | pending; v10 names no eligible candidate (pilot blocker) |
| TRT confinement/mutation/agent-path (#353) | pending, no retained record |
| seed commitment verification | pending; kept for shape parity, not applicable to a fresh commitment |
| MON-8/MON-12 matched-session acceptance (#519) | pending; #568 qualifies T1 oracle conformance only |

No machine gate failed because none ran; none was silently passed.

Transparency: while inspecting the private seed file format before drawing
the pilot seeds, the operator-side agent displayed the confirmatory v1 seed
values on the build host. They were not copied anywhere, and the pilot seeds
were drawn to be disjoint from them. The confirmatory commitment digest is
unchanged; whether that display matters is the owner's call.

## What unblocks the pilot

In order, and each is real software work rather than a registration change:
a paired non-oracle candidate bound through admission and execution (the #347
preflight comment's plan), a new BND candidate round that yields an eligible
stratum, and a live-provider admitted-session request path with a re-pinned
frontend profile. The registration above can be superseded by
`cse-causal-study-pilot-v2` when those exist; its seeds must not be reused by
any confirmatory declaration (ADR-66).
