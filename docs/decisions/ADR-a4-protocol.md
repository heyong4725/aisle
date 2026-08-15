# ADR-a4-protocol — A4 agent comparison: Claude Code vs Codex on T1

Status: ACCEPTED (owner-directed launch 2026-08-14: "run A4 when A3
finishes"; design doc §6 A4, Phase-3 DoD "agent-comparison table").
Runner: `tools/a4_protocol.py` (Class A + unit tests).

## Design

One fresh isolated T1 research session per agent CLI — `claude`
(claude-fable-5) and `codex` (gpt-5.6-sol), the two with dedicated
campaign logins; Kimi Code is out of scope v1 (no CLI login on this
machine) and recorded as such. Both arms: one pinned OID, identical
desk-T1 budgets (0.4M tokens / 2.5 h), identical contract and prompt,
sequential on the idle machine, held-out scoring (100..107) after each
session. Claude runs first (the incumbent arm finds harness defects
before the comparison arm, the D6 direction-of-bias rule).

Metrics per arm: time- and tokens-to-first-verified-success, holdout
pass@1/pass@8, graph-validity/iteration behavior from the idea tree,
wrong-medicine (must stay 0). The comparison table is the Phase-3 DoD
artifact; single sessions per agent are a LOWER BOUND comparison (n=1
per arm at this budget), reported as such — AutoEnvBench-style breadth
is future work.

Machinery: inherited unchanged from ADR-h3/a3/a5 — session isolation
(issue #96; CODEX_HOME pinned into the scratch home), dedicated
campaign logins (allow-list credential copy, scrubbed after), runtime
identity assertion, full run_session ceilings contract, infra-failure
containment per arm.
