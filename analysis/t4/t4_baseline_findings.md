# T4 dialogue baseline (ADR-32 increment one)

Pre-registered as idea I1 (branch feat/t4-dialogue): 10-episode sweep,
seeds 0..9, `harness rollout --graph graphs/expert_t4.yaml --tier T4`.
Corrected seeds (0/4/8, the seed%4==0 rule) exercise the full
request -> confirm -> correction -> re-confirm -> deliver-B loop; the
others confirm -> yes -> deliver-A.

The discriminating signature the pre-registration names: a machine that
IGNORES the correction delivers A against a goal whose target_med is B —
the frozen verifier scores that `wrong_object` on exactly the corrected
seeds. A machine that skips the confirm exchange entirely is caught
structurally (DIALOGUE_GOAL_LEAK keeps the goal away from the policy, so
there is nothing to grasp until the dialogue releases it) and behaviorally
(`dialogue_protocol` violations in feedback).

## Results (idea I1 closed `flat`, run 20260812-192152-d952b1)

| metric | value |
|---|---|
| pass@1 | **1.0** (10/10, seeds 0..9) |
| wrong_object | 0 |
| failures (any class) | 0 |
| retries | 0 on every episode |
| dialogue_corrections | 1 on exactly seeds 0/4/8; 0 elsewhere |
| t_end | 14.7–22.9 s (confirm exchange ≈ 2–3 sim s over the T1 profile) |
| wall | 228 s for the 10-episode sweep |

The pre-registration hit exactly: the correction fires on precisely the
seed%4==0 set, and against the A6-measured T1 teleport baseline (1.00 on
the same seeds) the scripted dialogue adds no failure mass — verdict
`flat`, which is the DESIGNED outcome for the expert. The tier's
difficulty for campaign agents is structural, not motor: an agent-authored
machine only passes the corrected seeds by actually processing the
correction (delivering A scores `wrong_object` against the B-target goal),
and DIALOGUE_GOAL_LEAK keeps the corrected answer out of the policy's
reach at the graph level, so there is no shortcut through the goal topic.

## Method notes

- The graph-level acceptance test (tests/graph/test_expert_graph.py::
  test_expert_t4_episode_succeeds) runs corrected seed 4 end-to-end and
  passed on its first execution: metformin requested, corrected to
  amoxicillin, delivered amoxicillin, dialogue_corrections=1.
- Measurement on an idle machine (wall-coupling rule, ADR-25 class).
