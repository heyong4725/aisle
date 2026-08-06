# ADR-M0 — Milestone M0 sign-off (SPEC 090, M0-6)

Status: SIGNED 2026-07-21 (option (a), M0-5 deferred); RE-AFFIRMED on
fresh evidence 2026-08-06 (option C, PR #93 — see the re-sign section).
Owner: @heyong4725.

This record is completed by the repo owner, not by an agent (CON-7,
M0-6). Agents may update the evidence table; only the owner fills the
verdict.

## Evidence

| Gate | Requirement | Result | Evidence |
|------|-------------|--------|----------|
| M0-1 | pass1 >= 0.95, 50 eps, seeds 0..49, macOS-arm64 | PASS: 0.980 (49/50) at sign-off (runs/m0-1-final, head 3644a50); re-affirmed 0.980 (m0-1-9be87d, PR #93 head) | runs/m0-1-final; runs/m0-1-9be87d |
| M0-2 | AMENDED (PR #88, ADR-26): the rerun independently satisfies M0-1's pass1 >= 0.95; goals bit-identical sans reset_sim_ns | PASS: 49/50 + 49/50, zero per-seed flips (m0_2_replicate.json) | runs/m0-2-116720 (vs m0-1-9be87d); original: runs/m0-2-final |
| M0-3 | committed env hash checks; mutation refuses rollout | authored | tests/accept/test_m0_gate.py::test_m0_3_mutated_frozen_file_refuses_rollout |
| M0-4 | trace_check --strict --specs 000-080 green | PASS | tests/unit/test_process_rules.py (CON-10/11/14/15 waivers retired) |
| M0-5 | so101 profile swap, pass1 >= 0.80 | BLOCKED — needs OWNER DECISION (see below) | tests/accept/test_m0_gate.py::test_m0_5_so101_profile_swap_pass1_at_least_80 (skip-marked) |
| M0-6 | this sign-off + frozen-set label | SIGNED 2026-07-21 (option (a)); re-affirmed 2026-08-06 | Owner verdict + re-affirmation below |

## M0-5 — owner decision required (does not have an agent-side resolution)

SPEC 090's M0-5 clause is now ASSET-GATED and explicitly deferrable (the
spec-change in this PR): M0 may be signed off with M0-5 deferred if the
owner records the choice here. M0-5 is blocked by TWO owner-side gates
that an agent cannot clear:

1. **Asset:** `assets/so101/so101.urdf` is absent; acquisition needs
   provenance/licensing sign-off (ADR-6, SCN-4).
2. **Node support:** ik-trajectory is franka-only (Panda FK, franka
   limits); so101 kinematics support is unwritten. The HAR-2 gate already
   refuses `--embodiment so101` up front (EMBODIMENT_MISMATCH) rather than
   running a doomed swap, and the M0-5 test's skip guard checks BOTH.

The milestone therefore cannot be closed green on M0-5 as things stand.
The owner MUST choose one at M0-6 sign-off (this is a CON-15 ambiguity the
agent surfaces rather than decides):

- (a) **Defer M0-5** past M0 (accept M0 on M0-1..M0-4/M0-6; M0-5 tracked as
  a follow-up gated on the asset + so101 node support), or
- (b) **Block M0** until the so101 asset and node support land and M0-5
  runs at pass1 >= 0.80.

## Owner verdict

- [x] **M0 accepted** with M0-5 **deferred** per option (a); follow-up issue: #13
- [ ] M0 blocked on M0-5 per option (b)
- [x] Frozen set labeled at the PR #12 squash-merge commit (see below)
- Date / signature: 2026-07-21 — @heyong4725 (authorized in session; PR #12 merge ratifies)

Decisions recorded:
- M0-1 accepted at **pass1 0.98** (49/50): clears the >= 0.95 bar; the single
  residual is a live-pipeline marginal artifact (ADR-12 §5c), not a grasp
  bug. A clean 50/50 is not required for M0.
- M0-5 (so101) deferred pending the asset + node support (issue #13).
- The neighbour-aware grip-axis policy (grip-axis fix) is retained.
- CON-7: the frozen set is stamped at the PR #12 merge commit; post-M0
  edits to the frozen set require human review.

## Notes

Evidence rows M0-1/M0-2 above are filled from the final-head (post-review)
50-seed runs on the merge candidate; M0-2 confirms the identical
per-episode status vector (CON-5 determinism).

## Re-sign — fresh evidence (option C, 2026-08-05)

Post-sign-off, two legitimate reviewed changes moved the expert's timing
path (ADR-25 reset anchoring, PR #80; the dora runtime bump, PR #85 —
the frozen set itself never changed), and M0-2's semantics were amended
from bit-identical status vectors to the statistical replicate gate
(PR #88, ADR-26: the rerun must independently satisfy M0-1's 0.95).
The owner chose a fresh-evidence re-sign over grandfathering the July
runs. Every fresh attempt is disclosed, including the failures:

| Attempt | Runs | Result | Disposition |
|---|---|---|---|
| 1 (2026-08-05) | m0-1-018e3b 47/50 | M0-1 FAIL 0.94 | operator load violation — H3 analysis ran concurrently; discarded as evidence, kept for disclosure |
| 1 cont. | m0-2-61cf1e 48/50 | (amended M0-2 logic verified live) | suite failed on M0-1 |
| 2, quiet box | m0-1-01575c 48/50, m0-2-0d0773 47/50 | M0-2 FAIL 0.94 | no orphan load, Fisher p=1.0 — a REAL margin loss, not noise. Diagnosis → issue #92: seed 3's "collision" was the hand assembly toppling the taller neighbouring box |
| 3, grasp-lift fix | 46/50 + 46/50 | M0-1+M0-2 FAIL | shallow grips reintroduced the T10 creep-rotation (drops); approach reverted |
| 4, hand-mount fix | m0-1-9be87d 49/50, m0-2-116720 49/50 | **M0-1 PASS 0.98, M0-2 PASS 0.98, zero per-seed flips** | root cause: the Franka hand is mounted -45 deg from the flange and the planning chain ignored it — every top-down grip was a diagonal pinch (HAND_MOUNT_YAW compensation, this PR) |

Findings the re-sign surfaced, on the record:

- The July M0-1 note attributed the seed-48 residual to "a live-pipeline
  marginal artifact (ADR-12 §5c), not a grasp bug." That attribution was
  WRONG: seed 48 was the diagonal pinch, and it passes under the mount
  fix. The new single residual is seed 36 (dropped ~18.9 s, identical in
  both runs — deterministic, tracked under issue #92's follow-up).
- The M0 gate at 0.95/50-episodes has ~0 margin when the true rate sits
  at 0.95 (the pre-fix state was a coin flip). At 0.98 true rate the
  suite is robust again; margin, not luck, is what the re-sign restores.
- Front-mode grasps do NOT yet carry the mount compensation — required
  before any T1 shelf evidence is collected (issue #92 follow-up).

### Owner re-affirmation

- [x] M0 re-affirmed on the fresh evidence above (M0-5 remains
  deferred per the original option (a))
- Date / signature: 2026-08-06 — @heyong4725 (option C chosen in
  session 2026-08-05; merge of PR #93 ratifies, as PR #12's merge did
  for the original sign-off)
