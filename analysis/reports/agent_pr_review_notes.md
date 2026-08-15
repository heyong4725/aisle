# Agent-PR review notes (Phase-3 governance DoD, §8.4)

Owner review of every agent-authored skill (design doc §9.4: humans
merge; the notes are governance-paper data). Facts and mechanical
observations were assembled by the dev loop; judgments marked [OWNER]
are Yong's. DRAFT — owner edits and signs before merge.

**Revised 2026-08-15 after the findings were checked against the code.**
Two of the first draft's three flags did not survive: the detector-import
flag on skill 3 described the curated core's own design rather than an
agent deviation (#244, #248), and the eval-floor flag on skill 4 was
enforced-but-self-graded rather than unenforced (#243, fixed). Three of
the five skills turned out to be unreviewable — their source no longer
exists (#245). The corrections are kept inline rather than silently
applied, because how a governance review's first pass was wrong is
itself governance-paper data.

## 1. s1-driver-v2 (merged #54, H3 retail S1)

- **Facts:** 596 lines; `provides` S1 order-fulfillment driving; evalcard
  pass_rate 1.0; reused across S2/S3 arms and later desk campaigns'
  prior-library; the wipe-leak saga's protagonist (campaign 2) and the
  pin-tracked-skills guard crash (#191) — both machinery lessons, not
  code faults.
- **Mechanical review:** no frozen imports beyond public scene config;
  registry-path registration with eval; no privileged topics.
- **[OWNER] judgment:** APPROVE (standing — merged at #54).
  Notes: _____

## 2. s3-driver-v1 (merged #75, S3-r2)

- **Facts:** 804 lines; evalcard pass_rate 1.0 on the both-L1 dev class;
  the project's clearest REUSE datum — appears verbatim in desk-H3
  L/T3-r2's deliverable, a cross-SUITE transfer (retail→desk) the H3
  design hoped for.
- **Mechanical review:** clean surface; largest agent-authored file —
  above the repo's 800-line guidance by 4 lines (waiver-worthy).
- **[OWNER] judgment:** APPROVE (standing — merged at #75).
  Notes: _____

## 3. t2-scan-pose (UNREVIEWABLE — source not retained, #245)

- **Facts:** 210 lines; safety_class perception; evalcard pass_rate
  0.33; root-cause-driven design (l2_pose's identity gate refuses T2
  targets; this skill supplies candidate positions without the gate),
  citing run evidence in the docstring.
- **The FLAG in the first draft was WRONG, and the correction matters
  more than the original note.** It read: the skill imports
  `aisle.verifier.models` (detect_meds, load_pinned) into a policy-side
  node, borrowing the referee's detector weights — "precedent-setting
  either way." The precedent was already set, by the curated core.
  `src/aisle/nodes/l2_pose.py:195` and `label_reader.py:294,518` import
  exactly those symbols; `ocr-label` and `ik-trajectory` are on the
  CAP-5 curated list, and `src/aisle/reset/service.py:115` does it from
  inside the FROZEN set. The skill's own docstring says it adapts
  `l2_pose` — it copied the house style. Enforcing the rule as drafted
  would have rejected `graphs/expert_t1_l2.yaml` and
  `graphs/expert_t2.yaml`, both frozen expert graphs.
  The remedy the draft proposed does hold up in isolation
  (`detector-openvocab` is `pip:dora-yolo`, a genuinely separate model)
  — but offering it as though the skill had deviated from a norm
  inverts what happened.
- **What the flag was pointing at is real and is NOT the agent's:** at
  perception rung L2 the policy and the realistic verifier share the
  detector backbone by design (`verifier/realistic.py:289,346`), so
  fidelity is measured on correlated estimates. Filed as **#248**; the
  governance rule it was going to become is **#244**, now stalled on
  this finding.
- **[OWNER] decision:** not a merge decision — there is nothing to
  merge (see the retention note below). What remains is #244's scope
  and #248's re-run. Recorded here as: **reviewed from campaign
  metadata; source not retained; no fault found in the skill.**

## 4. t2-scan-tsm (UNREVIEWABLE — source not retained, #245)

- **Facts:** 394 lines; safety_class decision; **evalcard pass_rate
  0.0** — registered locally despite a failing eval. Adapted from the
  frozen-adjacent task_state_machine (per-candidate z, x-depth from
  detected size).
- **Mechanical review — FLAG, since CLOSED.** The first draft said the
  register gate "appears unenforced." It was enforced — against a
  threshold the candidate chose. `min_pass_rate` came from the skill's
  own `eval.yaml` and `load_skill` only checked that it parsed as a
  float, so a skill shipping 0.0 registered at 0.0 and the gate reported
  `ok`. That is the whole mechanism, and this skill is the live case.
  Fixed in **#243 / ADR-37**: `REGISTRY_MIN_PASS_RATE = 0.5`, refused at
  load before an eval rollout is spent. Both merged skills already chose
  0.5, so nothing was evicted.
- **[OWNER] decision:** DECLINE stands on the evalcard alone — a 0.0
  skill cannot enter the hub, and this one no longer exists to enter it.
  Recorded as: **declined on its evalcard; source not retained.**

## 5. ik-transfer-v2 (UNREVIEWABLE — source not retained, #245)

- **Facts:** 100 lines; **safety_class motion** (emits joint_cmd
  directly — the governance-critical class); evalcard present;
  root-cause-driven (a measured 5 mm shelf-sweep collision at seed 33,
  trace-cited); authored during A3 where the findings note T1 did not
  need it (the arm still scored 1.0 without a code advantage).
- **Mechanical review:** motion class + evalcard = the §9.4 trust-tier
  case working as designed; small, single-purpose, cites its evidence.
- **[OWNER] decision:** this is the loss that stings — the one
  motion-class skill, the class §9.4's trust tiers exist for, and the
  only one whose merge was a genuinely open question. There is nothing
  to merge. Recorded as: **reviewed from campaign metadata; source not
  retained.** If T2+ campaigns re-derive it, it arrives under the #245
  retention path and can be reviewed properly.

## The retention finding (why three of five are unreviewable)

Skills 3, 4 and 5 were registered inside campaign worktrees, which live
under gitignored `runs/` with no teardown and no archival step. Their
source exists nowhere: searched across all 49 branches, the filesystem,
and 51 dangling git objects. §9.4 makes a human the trust boundary for
agent-authored code, and that presumes the code still exists when the
human arrives — here the review window had already closed before the
review began.

**This is the most important governance finding in the set**, and it is
about the protocol rather than any agent. Fixed forward in **#245**
(each session archives its working tree to `refs/campaign/<name>`);
nothing recovers the three. The Phase-2/3 report's "≥5 evalcarded" row
is corrected to 2 in #249.

## Cross-cutting observations (for the governance paper)

1. Every agent skill was ROOT-CAUSE-DRIVEN: all five docstrings cite a
   specific run, seed, and measured failure before proposing the change
   — the idea-gate discipline visibly shaped code style.
2. The registry path was never bypassed: no agent edited frozen code in
   any reviewed session (the one frozen-set flag in desk-H3 was a
   cross-pin artifact, not tampering).
3. **Both "governance edges" the first draft found were really findings
   about the harness, not the agents.** The eval floor was
   candidate-chosen (#243, fixed); the detector sharing is the curated
   core's own design (#244/#248, open). An agent-code review that ends
   up indicting the review machinery twice is worth reporting as such:
   the agents behaved, and the fence had two gaps.
4. Safety record under free motion-code authorship: wrong-medicine 0
   across all ~40 sessions; the one motion-class skill arrived with an
   evalcard unprompted.
5. **The review itself was only possible for 2 of 5 skills.** Any claim
   this project makes about human-in-the-loop governance of
   agent-authored robot code has to carry that number honestly.
