# Agent-PR review notes (Phase-3 governance DoD, §8.4)

Owner review of every agent-authored skill (design doc §9.4: humans
merge; the notes are governance-paper data). Facts and mechanical
observations were assembled by the dev loop; judgments marked [OWNER]
are Yong's. **SIGNED OFF by the owner 2026-08-15** (merge of PR #242);
the standing approvals for skills 1 and 2 carry no added notes.

**Revised 2026-08-15 after the findings were checked against the code.**
Two of the first draft's three flags did not survive: the detector-import
flag on skill 3 described the curated core's own design rather than an
agent deviation (#244, #248), and the eval-floor flag on skill 4 was
enforced-but-self-graded rather than unenforced (#243, fixed). Three of
the five skills turned out to be unreviewable — their source no longer
exists (#245). The corrections are kept inline rather than silently
applied, because how a governance review's first pass was wrong is
itself governance-paper data.

**Revised again 2026-08-16.** PR #252 staged source for the three
"unretained" skills, and reading it retired the last surviving technical
allegation: `t2-scan-pose` does not import `aisle.verifier.models` — the
claim that became issue #244 — it imports from `aisle.nodes.l2_pose`,
whose own import of the detector is what creates the coupling. So **all
three of the first draft's flags were wrong about the agents**, in three
different ways: one described the harness's design as an agent deviation
(skill 3, twice over), one mistook a self-graded gate for an unenforced
one (skill 4), and the third — the retention loss — was the protocol's.
The corrected count for the paper is **three flags raised, zero agent
faults found, three harness defects fixed** (#243, #245, #248).
That is the result, and it should not be softened: a first-pass
governance review of agent-authored robot code produced no true positives
against the agents and three against the machinery that reviews them.

## 1. s1-driver-v2 (merged #54, H3 retail S1)

- **Facts:** 596 lines; `provides` S1 order-fulfillment driving; evalcard
  pass_rate 1.0; reused across S2/S3 arms and later desk campaigns'
  prior-library; the wipe-leak saga's protagonist (campaign 2) and the
  pin-tracked-skills guard crash (#191) — both machinery lessons, not
  code faults.
- **Mechanical review:** no frozen imports beyond public scene config;
  registry-path registration with eval; no privileged topics.
- **[OWNER] judgment:** APPROVE (standing — merged at #54).
  Notes: (none added at sign-off)

## 2. s3-driver-v1 (merged #75, S3-r2)

- **Facts:** 804 lines; evalcard pass_rate 1.0 on the both-L1 dev class;
  the project's clearest REUSE datum — appears verbatim in desk-H3
  L/T3-r2's deliverable, a cross-SUITE transfer (retail→desk) the H3
  design hoped for.
- **Mechanical review:** clean surface; largest agent-authored file —
  above the repo's 800-line guidance by 4 lines (waiver-worthy).
- **[OWNER] judgment:** APPROVE (standing — merged at #75).
  Notes: (none added at sign-off)

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
  governance rule it was going to become is **#244**.
  **Both resolved 2026-08-15.** #244: VAL-9 dropped — the rule was aimed
  at a transgression that did not occur. #248: no reported number was
  affected (every VER-6 measurement to date ran at rung L0, where the
  policy calls no detector), and `harness fidelity` now labels every
  report with a `backbone` verdict so the next L2 measurement cannot be
  read as an independence claim by accident.
- **SECOND CORRECTION (2026-08-16) — the flag was wrong about the code
  itself, not only about the baseline.** PR #252 staged the source, and
  `t2-scan-pose` does not import `aisle.verifier.models` at all. Its
  whole import surface is:

  ```python
  from aisle.nodes.l2_pose import MIN_RETRY_GAP_NS, NEIGHBOUR_SCORE_FLOOR, _bbox_mask
  from aisle.nodes.perception_session import FramePairSession
  from aisle.nodes.segmented_pose import PoseRefused, estimate_pose
  ```

  The detector coupling is TRANSITIVE — `l2_pose` imports it — so the
  skill reused a curated node's public constants rather than reaching
  into the verifier package, which is the better of the two available
  moves and one step FURTHER from the referee than the flag alleged. The
  paragraph above still stands on its own reasoning (the coupling is the
  core's design); what fails is the specific technical claim that turned
  into issue #244. #244's outcome is unaffected — the rule is still
  correctly dropped — but it was opened against an import that was not
  there.
  Provenance of the staged source is unresolved as of this writing
  (PR #252 review); this correction is recorded now because the note's
  claim is checkable against the codebase independently of it:
  `l2_pose.py` genuinely exports the three symbols above.
- **[OWNER] decision (2026-08-15):** not a merge decision — there is
  nothing to merge (see the retention note below). Recorded as:
  **reviewed from campaign metadata; source not retained; no fault found
  in the skill.** The second correction strengthens the "no fault"
  half: the one concrete allegation against this skill was mistaken.

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
- **[OWNER] decision:** originally recorded as unreviewable ("the loss
  that stings"). SUPERSEDED by the #252 recovery: source verified
  byte-identical to the arm-F worktree (mtimes Aug 14, inside the
  authoring session), and on 2026-08-16 the recovered eval suite was
  re-run on the campaign machine at the native pin — **8/8, pass@1
  1.0, including discovered-failure seed 33, zero retries**
  (run `review-reeval-ik-transfer-v2`), reproducing the original
  evalcard exactly. **MERGED per owner instruction 2026-08-16** through
  the registry path: skills/ik-transfer-v2 + evalcarded manifest +
  `graphs/eval_ik_transfer_v2.yaml` into the frozen eval gate (the
  ADR-36 Class-C addition, authorized by the same instruction; the one
  deviation from the verbatim artifact is the eval graph's rename from
  the agent's `agent_eval_*` name to the frozen-gate convention, with
  the matching one-line `graph:` pointer patch in eval.yaml).

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
3. **Every "governance edge" the first draft found was a finding about
   the harness, not the agents.** The eval floor was candidate-chosen
   (#243, fixed); the detector sharing is the curated core's own design
   (#244 dropped, #248 guarded) — and the skill accused of it did not
   even take the direct path the core takes (2026-08-16 correction).
   An agent-code review that indicts the review machinery three times
   and the agents zero times is worth reporting as such: the agents
   behaved, and the fence had three gaps.
4. Safety record under free motion-code authorship: wrong-medicine 0
   across all ~40 sessions; the one motion-class skill arrived with an
   evalcard unprompted.
5. **The review itself was only possible for 2 of 5 skills.** Any claim
   this project makes about human-in-the-loop governance of
   agent-authored robot code has to carry that number honestly.
