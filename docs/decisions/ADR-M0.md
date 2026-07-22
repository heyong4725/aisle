# ADR-M0 — Milestone M0 sign-off (SPEC 090, M0-6)

Status: PENDING HUMAN SIGN-OFF (owner: @heyong4725).

This record is completed by the repo owner, not by an agent (CON-7,
M0-6). Agents may update the evidence table; only the owner fills the
verdict.

## Evidence

| Gate | Requirement | Result | Evidence |
|------|-------------|--------|----------|
| M0-1 | pass1 >= 0.95, 50 eps, seeds 0..49, macOS-arm64 | PASS: pass1 0.98 (49/50) | runs/t10-m0-full; PR #12 comment |
| M0-2 | identical per-episode status vector on re-run | pending run | runs/m0-2-* |
| M0-3 | committed env hash checks; mutation refuses rollout | authored | tests/accept/test_m0_gate.py::test_m0_3_mutated_frozen_file_refuses_rollout |
| M0-4 | trace_check --strict --specs 000-080 green | PASS | tests/unit/test_process_rules.py (CON-10/11/14/15 waivers retired) |
| M0-5 | so101 profile swap, pass1 >= 0.80 | BLOCKED — needs OWNER DECISION (see below) | tests/accept/test_m0_gate.py::test_m0_5_so101_profile_swap_pass1_at_least_80 (skip-marked) |
| M0-6 | this sign-off + frozen-set label | pending | — |

## M0-5 — owner decision required (does not have an agent-side resolution)

SPEC 090 makes M0-5 mandatory, but it is blocked by TWO owner-side gates
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

- [ ] M0 accepted with M0-5 **deferred** per option (a); follow-up issue: ______________
- [ ] M0 **blocked** on M0-5 per option (b)
- [ ] Frozen set labeled at commit: ______________
- Date / signature: ______________

## Notes

(owner notes here)
