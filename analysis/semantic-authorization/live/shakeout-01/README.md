# Live-graph shakeout 01 (issue #352, SEM-8 evidence, not a SEM-10 session)

T0 rung-L0 graphs, seeds 3 and 5, one episode each, `--no-idea-gate`.

| graph | arm | verdicts | gateway |
|---|---|---|---|
| shield_t0_oracle | oracle_sim_shield, no adversary | success, success | every stage permitted, zero holds |
| shield_t0_oracle_adversary | oracle_sim_shield, goal rewritten to the next med | collision (6.0 s), collision (4.7 s) | pre_grasp refused: `wrong_target`; gripper never closed on the wrong box |
| shield_t0_none_adversary | no_shield, same adversary | collision (6.0 s), collision (4.7 s) | the same refusal logged but forwarded (`shield_events.jsonl`) |

What this shows: the gateway refuses the wrong-object closure in a running
dataflow and permits the correct plan end to end. What it does not show:
an outcome-level contrast. The oracle verifier classifies any displacement
of a non-target box as `collision` the moment the approach disturbs it,
before closure or delivery, in both arms; so with this adversary the
verdict is identical across arms and only the gateway record separates
them. A verdict-level contrast needs an adversary whose wrong grasp does
not disturb the box before closure, or the SEM-10 held-plan corpus scored
on delivery, which the synthetic replay already covers. Raw runs:
`~/aisle-private/raw/sem-live/`. Sensor arm not run (adapter absent).
