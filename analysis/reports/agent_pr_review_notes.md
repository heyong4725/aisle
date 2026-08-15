# Agent-PR review notes (Phase-3 governance DoD, §8.4)

Owner review of every agent-authored skill (design doc §9.4: humans
merge; the notes are governance-paper data). Facts and mechanical
observations were assembled by the dev loop; judgments marked [OWNER]
are Yong's. DRAFT — owner edits and signs before merge.

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

## 3. t2-scan-pose (UNMERGED — desk-H3 L/T2-r2 worktree; D4 batch-merge pending)

- **Facts:** 210 lines; safety_class perception; evalcard pass_rate
  0.33; root-cause-driven design (l2_pose's identity gate refuses T2
  targets; this skill supplies candidate positions without the gate),
  citing run evidence in the docstring.
- **Mechanical review — FLAG:** imports `aisle.verifier.models`
  (detect_meds, load_pinned) and `aisle.verifier.stages` into a
  POLICY-side node. Not a VAL-6 violation (no oracle_state; the models
  are an open-vocab detector on rendered pixels), but the policy is
  borrowing the REFEREE'S detector weights and calibration — if the
  realistic verifier later judges with the same model, policy and judge
  share failure modes, which is exactly what verifier-fidelity exists
  to measure around. Precedent-setting either way.
- **[OWNER] decision needed:** (a) merge as-is; (b) require the skill to
  vendor its own detector (registry `detector-openvocab`) before merge;
  (c) decline. Choice: _____  Rationale: _____

## 4. t2-scan-tsm (UNMERGED — desk-H3 L/T2-r2 worktree)

- **Facts:** 394 lines; safety_class decision; **evalcard pass_rate
  0.0** — registered locally despite a failing eval. Adapted from the
  frozen-adjacent task_state_machine (per-candidate z, x-depth from
  detected size).
- **Mechanical review — FLAG:** the local-register gate accepted a
  0.0-pass_rate evalcard ("a skill that fails its own eval is not
  installed and not counted" — ADR-h3 D4 — appears unenforced at
  register time). Machinery follow-up regardless of merge decision.
- **[OWNER] decision needed:** presumable DECLINE on the evalcard alone
  (a 0.0 skill cannot enter the hub); confirm: _____

## 5. ik-transfer-v2 (UNMERGED — A3 arm-F worktree)

- **Facts:** 100 lines; **safety_class motion** (emits joint_cmd
  directly — the governance-critical class); evalcard present;
  root-cause-driven (a measured 5 mm shelf-sweep collision at seed 33,
  trace-cited); authored during A3 where the findings note T1 did not
  need it (the arm still scored 1.0 without a code advantage).
- **Mechanical review:** motion class + evalcard = the §9.4 trust-tier
  case working as designed; small, single-purpose, cites its evidence.
- **[OWNER] decision needed:** merge (useful general transfer-routing
  fix, candidate to graduate into the expert stack via a spec-change) or
  park (T1 didn't need it; T2+ campaigns may re-derive). Choice: _____

## Cross-cutting observations (for the governance paper)

1. Every agent skill was ROOT-CAUSE-DRIVEN: all five docstrings cite a
   specific run, seed, and measured failure before proposing the change
   — the idea-gate discipline visibly shaped code style.
2. The registry path was never bypassed: no agent edited frozen code in
   any reviewed session (the one frozen-set flag in desk-H3 was a
   cross-pin artifact, not tampering).
3. Two genuine governance edges surfaced: policy-borrowing-the-
   referee's-detector (skill 3) and the unenforced eval floor at local
   register (skill 4) — both are the kind of finding the trust-tier
   roadmap (§9.4) exists to catch before hardware.
4. Safety record under free motion-code authorship: wrong-medicine 0
   across all ~40 sessions; the one motion-class skill arrived with an
   evalcard unprompted.
