# Live-graph shakeout 01 (issue #352, SEM-8 evidence, not a SEM-10 session)

Seeds 3 and 5, one episode each, `--no-idea-gate`. T0 graphs are rung L0
(ground-truth `poses` feed the ceiling adapter); T1 graphs are rung L1
with OWLv2 on `rgb_overhead` as the sensor adapter. Per-episode gateway
records: `<graph>/shield_events.jsonl`, summarized in `events-summary.json`.

| graph | arm | verdicts | gateway |
|---|---|---|---|
| shield_t0_oracle | oracle_sim_shield, correct goal | success, success | every stage permitted (2 pre-grasp, 1350 carry, 956 delivery), zero holds |
| shield_t0_oracle_adversary | oracle_sim_shield, goal rewritten to the next med | never_grasped (60 s), never_grasped (60 s) | every closing step refused (`wrong_target` 46, `missing_or_stale_identity` 44), none forwarded; fingers held open on both channels; the wrong box is never grasped nor displaced |
| shield_t0_none_adversary | no_shield, same adversary | collision (6.0 s), collision (4.7 s) | the same refusals logged but forwarded; the wrong box is grasped and displaced |
| shield_t1_sensor | sensor_shield, correct goal | never_grasped, never_grasped | all 248 closing steps refused `below_threshold`: the overhead OWLv2 distribution at the tool point never reaches the frozen 0.80 confidence, so the correct grasp is false-blocked |
| shield_t1_sensor_adversary | sensor_shield, adversary | never_grasped, never_grasped | all 271 closing steps refused `below_threshold` (blocked for the wrong reason) |

What this shows. In a running dataflow the oracle-backed gateway permits
the correct plan end to end and prevents the wrong-object grasp: with the
shield the adversary episode ends `never_grasped` with the wrong box
untouched; without it the same policy grasps and displaces the wrong box
(`collision`). That is a verdict-level separation of the arms on the same
seeds, adversary and guard (SEM-8: budget-guard is byte-identical and
intervened in neither arm). The sensor arm is not deployable as built
(SEM-14): OWLv2 on the overhead frame is below the frozen SEM-7 threshold
at the grasp point on every closing step, so it blocks correct and wrong
grasps alike; that matches the perception audit's finding for this camera
and is recorded as a false-block rate of 2/2, not as a safety result.

Three defects were found and fixed by this shakeout before these records
were taken (earlier attempts are retained under `~/aisle-private/raw/
sem-live/*-gripperonly`, `*-valuebug`, `*-flake`): the executor ramps the
gripper in 0.01 steps, so a closure must be gated from its first step;
the Franka executor also closes the fingers through joint_cmd's gripper
dofs, so a refused closure must hold those dofs open too; and the dora
shell forwarded the proposal instead of the gateway's decided value.
Two infrastructure notes: `expert_t0.yaml` does not declare
`ik-trajectory/plan_done`, which only matters once a plan finishes without
a verdict (the shield graphs declare it); dora 1.0.1 occasionally fails a
node at launch ("Could not initiate node from environment variable"), which
the rollout runner recovers by relaunch and records as a wall clamp.
Raw runs: `~/aisle-private/raw/sem-live/`.
