# ADR-a3-protocol — A3 ablation: params-only vs params+code authorship

Status: PROPOSED (drafted 2026-08-12 by the dev loop; awaiting owner nod
before any session spends budget — protocol ADRs h1..h4 were each
owner-accepted before their campaigns ran). Design doc §6 ablation table:
"A3: params-only vs. params+code authorship"; Phase-2 DoD lists the A3
table. Reuses the ADR-h1/h2 single-session machinery (tools/campaign.py)
and every ratified admissibility rule from ADR-h3 (§§1–6 as applicable
to single sessions).

## Question

Does the agent's edge come from authoring CODE (new nodes, skills), or
from searching the registry and tuning parameters over a fixed
capability set? ENPIRE-style substrates constrain agents to composition;
if the params-only arm matches the full arm on T1, the schema tax bought
containment for free; if it falls far behind, code authorship is doing
real work (H4 territory).

## Design (proposed)

Two arms, one tier (T1, the measured 0.9–1.0 expert baseline keeps
motor difficulty out of the contrast), same model/CLI, one pinned OID,
fresh isolated sessions (issue #96 rules), sequential on the idle
machine:

- **Arm F (full).** The standard `harness/CLAUDE.research.md` verbatim —
  compose, author nodes/skills, register through review.
- **Arm P (params-only).** The same contract with one appended hard
  rule, committed on the arm's worktree BEFORE the session (the
  contract is versioned by design; the diff and its sha256 are the
  treatment and ride the record):

  > PARAMS-ONLY VARIANT (A3): you MUST NOT author or edit node code or
  > skills — no new or modified files under `src/` or `skills/`, no new
  > Python anywhere, and `harness skill register` is off-limits. Your
  > action space is registry search, dataflow YAML composition and
  > wiring, node `env` parameters in the graph, and rollout
  > configuration.

Enforcement is audit-backed, not honor-based: the session's final
worktree diff against the pin must touch nothing under `src/` or
`skills/` (machinery mirrors the H3 frozen audit); a violating session
records `params_leak` and the cell is excluded and rerun, never
direction-assumed (the ADR-h3 campaign-2 rule).

- **Budgets per arm:** 0.4M tokens / 40 episodes / 2.5 h (the desk-suite
  T1 split — the same session shape H2 measured at 33–86 min).
- **Metrics:** time- and tokens-to-first-verified-success; pass@1/pass@8
  of the deliverable graph on the held-out seeds (100..107, clean
  scoring worktree); graph-validity rate; wrong-medicine (must stay 0).
- **Order:** F first, then P — a harness defect found on F biases the
  comparison AGAINST the novel arm's disadvantage claim, mirroring
  ADR-h3 D6's direction-of-bias reasoning.
- **Seeds:** dev 0..49 / held-out 100..107, identical both arms.

## Consequences

- `tools/a3_protocol.py` (Class A + unit tests) drives both sessions via
  tools/campaign.py; results land under `analysis/a3/` with the same
  cells/flags/verdict discipline as H3 (fail-closed admissibility).
- The contract diff mechanism deliberately reuses the ADR-h3 §1 rule
  ("mid-campaign help only via committed CLAUDE.research.md diffs") —
  here the diff IS the treatment, applied before the session, recorded
  in the treatment block.
- Not in scope: multi-tier params-only ladders (a later extension if the
  T1 contrast is interesting).

## Alternatives considered

- Prompt-level restriction without a committed contract diff: rejected —
  unauditable, and the contract file is the versioned channel the
  protocol already trusts.
- Reusing H1/H2 session records as the F arm: rejected — different pins
  and runtime eras; the ADR-h3 §5 runtime-identity lesson says never
  contrast across environments.
