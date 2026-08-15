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

## Phase 3 DoD (§8.4) — complete except one owner item

| DoD item | artifact | headline |
|---|---|---|
| H3 plot (ASPIRE ablation) | analysis/h3/desk (+ retail analysis/h3) | desk T1→T4 two-arm campaign: verdict UNDECIDED under strict admissibility; no speedup measured — the ladder's difficulty spacing (T1/T4 easy for both arms, T2/T3 beyond both) is the finding; skill-reuse mechanism verified live |
| fleet-scaling plots (A5) | analysis/a5 (findings + SVG) | throughput 1.6/4.1/4.3 succ/hr at N=1/4/8 — knee at ~4 lanes/host; token super-linearity +22%/+31%; quality contention-invariant |
| agent-comparison table (A4) | analysis/a4/a4_findings.md | both agents solve T1 at 1.0/1.0; Claude ~2× cheaper end-to-end; Codex faster to first success then over-iterates (n=1/arm) |
| cross-embodiment table | SO-101 profile, M0-5 gate, ADR-27 lineage | profile swap ≥0.80; variant nodes documented |
| skill library ≥5 evalcarded | registry + skills/ | s1-driver-v2, s3-driver-v1, t2-scan-pose, t2-scan-tsm, ik-transfer-v2 (5; more in campaign worktrees pending review) |
| **agent PRs reviewed with written notes** | **OPEN — owner** | the governance-paper data; the one outstanding DoD item |

## The two headline claims, as measured

- **H5 (structural safety): unbroken.** Wrong-medicine deliveries: 0 —
  every tier, fleet size, agent CLI, and campaign arm, under free
  agent-authored motion code throughout.
- **H4/substrate:** supported on efficiency at T1 (A3), stress-tested by
  H3's null (the substrate did not make hard tiers easy — honest scope).

## Known-open ledger (for the next session)

Analyzer follow-ups: wipe_leak pin-tracked-skill semantics; W/T2
treatment_drift derivation trace; T2-class holdout scoring windows;
campaign_metrics ancestry rule for treatment-commit arms (A3 arm-P
artifact). Infra: sim-lockstep into the rollout path (fleet fidelity);
codex mid-session token-counter drift. Science: T2/T3 remain unsolved
by any arm at session budgets — the standing challenge the tiers were
built to pose.
