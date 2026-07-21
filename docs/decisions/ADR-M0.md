# ADR-M0 — Milestone M0 sign-off (SPEC 090, M0-6)

Status: PENDING HUMAN SIGN-OFF (owner: @heyong4725).

This record is completed by the repo owner, not by an agent (CON-7,
M0-6). Agents may update the evidence table; only the owner fills the
verdict.

## Evidence

| Gate | Requirement | Result | Evidence |
|------|-------------|--------|----------|
| M0-1 | pass1 >= 0.95, 50 eps, seeds 0..49, macOS-arm64 | pending run | runs/m0-1-* |
| M0-2 | identical per-episode status vector on re-run | pending run | runs/m0-2-* |
| M0-3 | committed env hash checks; mutation refuses rollout | authored | tests/accept/test_m0_gate.py::test_m0_3_mutated_frozen_file_refuses_rollout |
| M0-4 | trace_check --strict --specs 000-080 green | PASS | tests/unit/test_process_rules.py (CON-10/11/14/15 waivers retired) |
| M0-5 | so101 profile swap, pass1 >= 0.80 | BLOCKED: assets/so101 absent (ADR-6) | tests/accept/test_m0_gate.py::test_m0_5_so101_profile_swap_pass1_at_least_80 (skip-marked) |
| M0-6 | this sign-off + frozen-set label | pending | — |

## Owner verdict

- [ ] M0 accepted (M0-5 waived / deferred: ______________)
- [ ] Frozen set labeled at commit: ______________
- Date / signature: ______________

## Notes

(owner notes here)
