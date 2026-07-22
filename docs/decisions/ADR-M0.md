# ADR-M0 — Milestone M0 sign-off (SPEC 090, M0-6)

Status: PENDING HUMAN SIGN-OFF (owner: @heyong4725).

This record is completed by the repo owner, not by an agent (CON-7,
M0-6). Agents may update the evidence table; only the owner fills the
verdict.

## Evidence

| Gate | Requirement | Result | Evidence |
|------|-------------|--------|----------|
| M0-1 | pass1 >= 0.95, 50 eps, seeds 0..49, macOS-arm64 | PASS: pass1 0.980 (49/50) on final head 3644a50 | runs/m0-1-final |
| M0-2 | identical per-episode status vector on re-run | PASS: identical (seed, status) vector; both pass1 0.980 | runs/m0-2-final (vs m0-1-final) |
| M0-3 | committed env hash checks; mutation refuses rollout | authored | tests/accept/test_m0_gate.py::test_m0_3_mutated_frozen_file_refuses_rollout |
| M0-4 | trace_check --strict --specs 000-080 green | PASS | tests/unit/test_process_rules.py (CON-10/11/14/15 waivers retired) |
| M0-5 | so101 profile swap, pass1 >= 0.80 | BLOCKED — needs OWNER DECISION (see below) | tests/accept/test_m0_gate.py::test_m0_5_so101_profile_swap_pass1_at_least_80 (skip-marked) |
| M0-6 | this sign-off + frozen-set label | pending | — |

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
