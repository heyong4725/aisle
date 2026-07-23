# ADR-17 — Retail capability registry extension (T14, design doc §11.4)

Status: accepted (CON-15: §11.4 names the capabilities but not their
manifest shapes, rungs, or sources; the agent picks and records).
Task: T14. Specs: 050 (CAP-1..6), 200 (RS-10 ladder). Relates to
[[ADR-15]] (planogram), [[ADR-16]] (retail verifier).

## Decisions

1. **Eight manifests, no new schemas.** Every §11.4 topic fits the
   existing CAP-2 vocabulary: reports/goals are `json_utf8` (all ≤ 10 Hz,
   CON-4), placement targets are `pose7d_f32`, base topics are the T11
   `base_*` schemas. No Class C vocabulary change.
2. **base-driver-sim is a bridge FACET** (the arm-driver-sim pattern):
   source = `dora_genesis.py`, provides `base_actuation`. It is
   motion-class, so per CAP-6 it ships WITH an evalcard — the T11 mobile
   conformance suite (`tests/accept/test_contract_mobile.py`), which
   verifies exactly its contract (MOB-1 rates/schemas/metadata).
3. **waypoint-nav's current implementation is the T11 nav-action node**
   (source = `nav_action.py`, provides `waypoint_navigation`): it already
   plans base motion between named store locations (MOB-2). A future
   collision-aware planner swaps behind the same manifest. It emits
   `base_cmd` for the GUARD (decision-class; the motion gate is the
   guard, VAL-5/MOB-3).
4. **Oracle rungs consume ground truth explicitly (RS-10):**
   `order-reader` reads the order from `episode_goal`; `stock-detector`
   and `misplacement-detector` diff `oracle_state` against the planogram
   using the SAME occupancy geometry as the verifier
   (`aisle.verifier.retail` helpers — detector and referee cannot drift).
   Realistic rungs (OCR/VLM, shelf-label vision) swap behind the same
   manifests later, per the ladder.
5. **placement-controller is decision-class** (the grasp-planner
   pattern): it emits a slot-template `target_pose` for the existing
   ik/guard/driver stack; it does not command motion directly.
6. **task-planner is the agent-iteration surface**: the hub-origin stub
   turns an episode goal into a deterministic subtask sequence
   (goto/pick/place triples per order line, restock, or return); agents
   are expected to replace it (§11.4).
7. **Stubs are real, minimal, pure-cored nodes** (CON-12): each has a
   unit-tested pure core and a thin dora main. Graph-level exercise
   arrives with T15's S1 expert graph (`test_s1_expert`), the suite's
   integration gate.

## Consequences

- Registry completeness (CAP-5 test) grows from 16 to 24 ids.
- `eval: null` everywhere except base-driver-sim (CAP-6: all others are
  perception/decision class, hub origin).
- The deliberate no-rearrangement gap stays: none of the new capabilities
  provides a rearrangement skill.
