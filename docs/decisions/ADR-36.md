# ADR-36 — the skill gate is part of the environment

Status: ACCEPTED (issue #228). Widens the CON-7 frozen set; human sign-off
required per CON-10. Supersedes no ADR.

## Context

`tools/env_hash.py` froze `graphs/expert_*.yaml` and, since #197, their ADR-30
turn plans. Issue #219 made three more graphs lockstep participants, and none
matched that prefix, so the question of whether they belong in the frozen set
was being answered by a glob rather than on purpose.

Two of the three are not ordinary graphs:

| graph | what it is |
|---|---|
| `graphs/eval_s1_driver_v2.yaml` | the suite `skills/s1-driver-v2/eval.yaml` names |
| `graphs/eval_s3_driver_v1.yaml` | the suite `skills/s3-driver-v1/eval.yaml` names |
| `graphs/agent_campaign.yaml` | the research agent's own deliverable (`tools/campaign.py`) |

`harness skill register` rolls out through the graph named in the skill's
`eval.yaml` and writes a registry manifest only if that rollout passes
(CAP-6/CAP-7). So the `eval_*` graphs are the exam an agent-authored skill
sits to enter the registry.

They were editable by the candidate.

## Decision

**`graphs/eval_*.yaml` and their committed turn plans join the frozen set.**

A skill gate the candidate can edit is not a gate. That is the same argument
that put the verifier and the reset service inside the fence at M0, and the
same one ADR-33 used to pull the guard's verdict inputs in: the unit of the
fence is the thing a result depends on, not the directory it happens to live
in. An agent that can widen its own eval's tolerances, or drop the guard from
the eval graph, can register a skill that never passed anything.

The turn plans go with the graphs. Since #219 those graphs are lockstep
participants, and `nodes/turn_barrier.py` loads the committed plan at runtime
— it is executable scheduler topology for a measured run, not documentation
(the reasoning #197 already applied to the expert plans).

**`graphs/agent_campaign.yaml` deliberately stays OUT.** It is the artifact
`tools/campaign.py` tells the agent to keep pointed at its current best
system. Freezing it would put CON-7 in direct conflict with the experiment
the campaign is running. Its turn plan stays out for the same reason, and
#227 gives the agent `harness validate <graph> --write-turn-plan` so it can
keep that plan honest itself.

The set is now expressed as `FROZEN_GLOBS` rather than a literal buried in
`frozen_files()`, so the three places that need to agree can derive from one.

## Consequences

- `env_hash` moves once, here: 69 -> 73 files.
- Editing an eval graph or its plan becomes an `env-change` needing human
  review (CON-7), which is the intended cost. Skill *authors* are unaffected:
  a skill is `skills/<id>/`, and its eval config still names which frozen
  graph to run.
- **The campaign tamper audit was already blind.** `tools/campaign.py`
  re-listed `"graphs/expert_*.yaml"` by hand next to the shared constants, so
  when #197 froze `graphs/turn_plans/expert_*.json` the audit did not follow —
  an agent could have edited ADR-30 scheduler topology and `audit_frozen`
  would not have reported it. It derives from `FROZEN_GLOBS` now, with a test
  that it stays derived.
- The research contract's frozen-set list is checked against the globs too.
  It already had to name every `FROZEN_DIRS`/`FROZEN_FILES` entry; the globs
  were enumerated by hand, which is exactly how a widening under-describes
  what an agent may not touch.

## Alternatives considered

- **Leave all three out and document why.** Rejected for the `eval_*` pair:
  "documented" is what the guard's `metadata["error"]` filters were before
  ADR-34, and a gate whose integrity rests on nobody noticing it is editable
  is not a gate. Accepted for `agent_campaign`, where the reason is real.
- **Freeze all three, including `agent_campaign`.** Rejected: it makes the
  frozen set fight the experiment. The campaign explicitly instructs the
  agent to keep that graph current, so freezing it would refuse the loop
  every run.
- **Freeze the eval graphs but not their turn plans.** Rejected: the barrier
  loads the plan, so an unfrozen plan lets the scheduler topology of a gated
  run change without moving the hash — the gap #197 closed for the expert
  graphs, reopened for the ones that decide registry admission.
