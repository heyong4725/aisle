# T2 breakthrough r3 — agent 3 (fleet 4) campaign notes

Status: IN PROGRESS (will be finalized before session end).

## What this campaign inherited

- Stock expert_t2: pass@1 0.08 (2/25), 0 wrong_object ever. Failures:
  never_grasped (tour starvation from l2_pose identity gate) > collision
  (transit sweeps) > dropped. See analysis/t2/t2_curve_findings.md.
- r2 skills `t2-scan-pose` (positions without identity gate,
  tour_candidates with per-candidate z) + `t2-scan-tsm` (per-candidate z
  tour, board-snap grasp z, planner slot re-aim, confirm-margin 0.10
  double read, 2 tour restarts, pitched-first low faces). Sources were
  staged in skills_pending_review/ (provenance verified) but NOT in the
  registry — the ADR-37 0.5 floor closed r2's min_pass_rate:0.0 hole.

## Key mechanics learned this round (read this before touching anything)

1. **Unregistered node ids cannot roll out** (MANIFEST_MISSING /
   PATH_MANIFEST_MISMATCH). The escape hatch is **ADR-40 sandbox
   registration**: `harness skill register skills/<x> --sandbox` admits
   the id for validation with no eval run — allowed for non-motion
   safety classes. eval.yaml still needs min_pass_rate >= 0.5 declared.
2. **The r2 skill mains predate ADR-30 lockstep.** They used
   `from dora import Node` → the turn barrier watchdogs out.
   Port = import `aisle.turn_node.Node` + declare `turn` input /
   `turn_done` output in skill.yaml + mark tsm feedback inputs
   `turn_edge: episodic` (else CLOCK_CYCLE) + **never emit from wall
   `tick` events**: derive the 1 Hz feedback from `turn` sim time
   exactly like the stock task_state_machine main does (emitting from a
   wall handler raises ProtocolError and kills the node — cost us one
   8-episode reservation to learn).
   Second pitfall: r2's ScanTourMachine.on_target_pose stored the FULL
   target_pose metadata (turn stamps included) in tour_meta and replayed
   it on the promoted grasp_target many turns later →
   "supplied an unrelated turn stamp" ProtocolError, node death,
   barrier watchdog. Fix: strip TURN_STAMP_KEYS from tour_meta (stock
   task_state_machine already does this; the wrapper stamps the
   emitting turn itself).
3. Node id in graph must equal skill id; regenerate
   `graphs/turn_plans/agent_campaign.json` via
   `harness validate <graph> --write-turn-plan` after any topology edit.

## Idea log (see runs/ideas for verdicts)

- I1: lockstep port of r2 skills + sandbox registration, baseline on
  seeds 0..7. (pending)

## What I'd try next (updated as results come in)

- pending baseline failure taxonomy.
