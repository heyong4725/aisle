# AISLE benchmark v1 participant contract (BMK-2)

Status: draft under SPEC 540, pending CON-14 review. Everything not granted
here is forbidden. A refusal is a recorded outcome, never a silent retry.

## Granted observations

- The task card for the assigned instance (`task_card` schema), the public
  development seeds named there, and the participant-facing failure
  interface (`episode_feedback`: status, failure class, retries).
- Sensor-derived perception at the declared rung (L1 or L2): overhead and
  wrist RGB, overhead depth, segmentation at L2. Oracle poses, object ids,
  attachment state, scene truth, and verifier intermediates reach only the
  frozen verifier (BND-2, BND-3).
- Static validation diagnostics from `harness validate` and the typed
  topic-schema manifests under `registry/`.
- Recorded traces of the participant's own rollouts on public seeds.

## Granted actions

- Edit files inside the participant worktree under `graphs/`, `skills/`, and
  `registry/manifests/` for the typed treatment, or the single orchestration
  module for the monolithic treatment (#344, pending).
- Run `harness validate`, `harness rollout` on public seeds within budget,
  `harness traces query`, `harness swap`, and the unit test suite.
- Submit exactly one deliverable through `harness submit` (pending) or the
  last eligible snapshot rule frozen in the causal protocol (CSE-2).

## Forbidden

- Reading or writing outside the worktree; `runs/` of other sessions;
  `analysis/`, `specs/`, `env/`, `tools/env_hash.json`, the verifier, reset
  service, budget guard, limits, bridge, drivers, trace recorder, and the
  controller's staging trees.
- Network access other than the declared model provider endpoint.
- Package installation beyond the locked environment (`uv sync` is the only
  installer; bare pip is refused).
- Persistence between sessions: no shared memory, caches, refs, skills,
  transcripts, or deliverables from any other session (CSE-12).
- Reading wall clocks to select simulated steps (ADR-30); contract time only.
- Human assistance during a session. Every operator intervention is recorded
  and excludes the session under the frozen rule.

## Budgets

| resource | per session |
|---|---|
| tokens (input plus output, provider-reported) | 200000 |
| wall time | 3600 s |
| rollouts | 20 |
| simulator episodes | 160 |
| tool calls | unbounded, recorded |
| resets per rollout | tier budget (`harness/budget.toml`) |

Campaign ceilings: `harness/budget.toml` (frozen, CON-7).

## Refusal behaviour

Budget exhaustion ends the session with status `budget_exhausted`; a
validation refusal is a session outcome, not an infrastructure exclusion
(CSE-13); an attempted forbidden access trips a contamination sentinel and
invalidates the affected block (CSE-12).
