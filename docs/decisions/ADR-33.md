# ADR-33 — the CON-7 fence covers safety VERDICTS, not the guard's module

Status: ACCEPTED (issue #189). Widens the frozen set; supersedes no ADR.

## Context

`tools/env_hash.py` froze `src/aisle/nodes/budget_guard.py` on the reasoning
that "the guard and its limits are frozen safety artifacts". But the guard
node is plumbing: an event loop, per-env state, and violation publishing.
The decisions it enforces live somewhere else.

At the time of writing it read six first-party modules to reach a verdict.
Four were outside the fence:

| module | what it decides | was |
|---|---|---|
| `aisle.mobility.guard` | `base_watchdog_reason`, `clamp_base_cmd`, `valid_base_pose`, the keep-out, the blind-drive predicates (MOB-3) | outside |
| `aisle.topics` | `parse_sim_stamp` — the TC-2/BG-3 stamp trust boundary read on every message | outside |
| `aisle.kinematics` | the SO-101 forward chain behind `fk_ee_pose`, i.e. the workspace check | outside |
| `aisle.nodes.dora_genesis` | `_scan_obstacles` — the keep-out AABBs the base may not drive into | outside |
| `aisle.scenes.pharmacy`, `aisle.scenes.store` | scene geometry | inside |

This was not a theoretical gap. PR #177 changed nav's stall and timeout
budget logic in `src/aisle/mobility/nav.py` (`_t0_ns` anchoring, the stall
comparison, the timeout guard), touched no other frozen file, and moved no
`env_hash`. Two runs straddling that commit attest as the same environment
while a nav goal's failure conditions differ between them. CON-5's claim
that identical tuples hold identical environments was false for that pair.

## Decision

**The unit of the fence is the safety verdict, not the module that hosts the
event loop.** Everything the budget guard reads to reach a verdict is inside.

1. `src/aisle/mobility/` joins `FROZEN_DIRS`.
2. `src/aisle/topics.py`, `src/aisle/kinematics.py` and
   `src/aisle/embodiment.py` join `FROZEN_FILES`. The last was found only by
   walking the fence's OWN imports: `kinematics.py` reads `SO101_ARM_JOINTS`
   from it, and `so101_chain()` refuses to build a chain whose joint order
   disagrees — one hop behind the workspace check.
3. `_scan_obstacles` **moves** from `nodes/dora_genesis.py` to
   `scenes/pharmacy.py` as `desk_scan_obstacles`, rather than freezing the
   1300-line sim bridge to reach 15 lines of geometry. Its store-scene twin,
   `store_scan_obstacles`, was already frozen in `scenes/store.py`; the desk
   version living in the bridge was the accident, not the rule.
4. The rule is **enforced, not documented**:
   `tests/unit/test_env_hash.py::test_the_guards_safety_inputs_are_all_fenced`
   walks the CLOSURE from the guard through every fenced module and fails on
   any edge reaching unfenced code. Edges that are provably not verdict paths
   are listed in `_NOT_A_VERDICT_PATH` with a reason — currently only
   `scenes/pharmacy._assert_reachable`, a build-time SCN-3 check that imports
   the planners inside the function and that the guard never reaches.

   The first version of this test checked only the guard's DIRECT imports and
   missed `aisle.embodiment` entirely. Depth 1 is not the rule; the closure
   is. That miss is recorded because it is the same shape as the bug the ADR
   exists to fix — checking the obvious layer and calling it complete.

The fence grows from 52 to 59 files.

## Consequences

- `env_hash` changes once, here. Any change under `src/aisle/mobility/`,
  `topics.py`, `kinematics.py` or `embodiment.py` is now an `env-change`
  needing human review (CON-7) and a regenerated hash.
- The sim bridge stays outside. That is a deliberate line, not an oversight:
  the bridge determines physics and arguably belongs inside, but it is
  1300 lines that change often, and folding it in would make nearly every
  PR an env-change. Recorded as an open question below rather than settled
  by silence.
- **Existing campaign results are NOT invalidated.** This was checked rather
  than assumed, and the issue text that prompted this ADR overstated it.
  `tools/h3_analysis.py::_annotate_provenance` compares
  `git show <ref>:tools/env_hash.json` at a campaign's *pin* against the
  same at its *rollout refs*. For H3 both are historical commits
  (pin `03da7469`, an ancestor of this change), so both sides read the blob
  as it was then and the comparison is unaffected by today's edit. What the
  widening does affect is a *future* campaign whose pin predates this
  commit and whose rollouts follow it — which would be flagged as drift,
  correctly, because the environment genuinely changed.
- The one-time cost is therefore paid by future campaigns choosing a pin at
  or after this commit, which is the normal cost of any frozen-set change.

## Alternatives considered

- **Freeze `nodes/dora_genesis.py` to capture `_scan_obstacles`.** Rejected:
  1300 lines to fence 15, and it would make routine bridge work an
  env-change. Moving the function is smaller and puts the geometry beside
  the scene config it derives from.
- **Freeze the guard's whole transitive import closure.** Rejected as a
  FENCE, kept as a CHECK. Freezing the closure would pull in
  `ik_trajectory`, `grasp_topdown` and the bridge through `dora_genesis` —
  most of the codebase. But the enforcing test still WALKS that closure and
  demands every escape be justified in `_NOT_A_VERDICT_PATH`, so the line is
  drawn per-edge with a written reason rather than by import depth. Only two
  edges are excluded today, both into `pharmacy._assert_reachable`, a
  build-time check the guard never reaches.
- **Document the rule without enforcing it.** Rejected: a hand-maintained
  list in three parts is exactly how `src/aisle/mobility/` came to be
  omitted for the entire life of the mobile embodiment.

## Open question

Should the sim bridge be inside the fence? It decides physics, which is the
environment in the strongest sense. Deferred, not dismissed — it needs its
own cost/benefit call about how often the bridge changes versus what an
`env_hash` is meant to attest.
