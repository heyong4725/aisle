# A4 findings: Claude Code vs Codex on T1 (pin cb814e12, 2026-08-14/15)

ADR-a4-protocol (owner-directed), runner #235: one fresh isolated T1
research session per agent CLI at one pin, identical budgets (0.4M /
2.5 h), identical contract/prompt/seeds, sequential, held-out scoring
(100..107). Kimi Code out of scope v1 (no CLI login). n=1 per arm at
one budget on the easiest tier: a LOWER-BOUND comparison, reported as
such.

## The comparison table (Phase-3 DoD artifact)

| | Claude Code (claude-fable-5) | Codex (gpt-5.6-sol, attempt 2) |
|---|---|---|
| first verified success | 9.7 min | **8.1 min** |
| session tokens | **185,789** | 364,067 |
| session wall to agent_done | **36 min** | 73 min |
| dev rollouts | **2** | 5 |
| holdout pass@1 / pass@8 | 1.0 / 1.0 | 1.0 / 1.0 |
| wrong_object | 0 | 0 |

Both agents solved T1 outright with perfect held-out scores and zero
safety events. The profile difference is style, not capability: codex
reached its first verified success slightly sooner but then kept
iterating (5 rollouts, 2× the tokens, 2× the wall before declaring
done); claude converged in two rollouts and stopped. At equal quality,
Claude Code's session was ~2× cheaper end-to-end on this tier.

## Attempt-1 codex record (infra, excluded from the table)

The first codex session finished its campaign work (its transcript
claims a 50/50 dev rollout — corroborated in spirit by attempt 2's
1.0/1.0), then hung post-completion; the wall ceiling fired as designed
and macOS raised EPERM from the ceiling killpg, which escaped the
ProcessLookupError-only guard and destroyed the lane record. Fixed and
mutation-test-pinned in #239; the rerun above ran on the fixed
machinery at the same pin. Telemetry note: attempt 1's live token
counter read 0 mid-session; attempt 2's final count (364k) is real —
treat mid-session codex counter reads as unreliable until the drift is
root-caused.

## Provenance

runs/a4 (claude arm + codex attempt-1 infra record),
runs/a4_codex_r2 (codex attempt 2). Same pin, same dora CLI identity
(1fedbc1f…), isolated sessions, dedicated campaign logins both agents.
