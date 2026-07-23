# ADR-16 — Retail verifier semantics (T13, SPEC 200 RS-4..9)

Status: accepted (CON-15: RS-4..9 fix the criteria and success rules but
not their geometric definitions or edge semantics; the agent picks and
records). Task: T13. Specs: 200, extends 040 (VER-1 discipline). Relates
to [[ADR-15]] (planogram/store model).

## Decisions

1. **One data-driven judge (RS-5).** `judge_retail` derives its
   requirements from GOAL PARAMETERS: an `order` enables the counter
   rules; `restock` entries require {slot: category}; `misplaced` entries
   require the NAMED item back in its `belongs_in` slot. No scenario
   branches in the verdict logic.
2. **Yaw vs front-face are independent axes (RS-4).** The yaw criterion
   measures LONG-AXIS alignment (yaw error folded mod 180° ≤ 10°); the
   front-face criterion is the sign (cos of the raw yaw error > 0 — the
   label faces the aisle). A box placed backward passes yaw and fails
   front-face; a box skewed 15° fails yaw and passes front-face — each
   criterion can fail alone, as the acceptance table requires.
3. **Alignment line = the slot's template front edge.** The "row
   alignment line" is where each item's front edge sits when placed at
   its template (template x + the item's own half-depth); the criterion is
   |item front edge − template front edge| ≤ 1.5 cm along the facing
   axis. Independent of pos (a 1.8 cm forward slide fails alignment alone
   at pos ≤ 2 cm) and of neighbors' current state (deterministic; an
   empty row cannot mask a misaligned item).
4. **Overhang** = the item's world extent along the facing axis past the
   unit's front edge (depth/2), with a small numeric epsilon
   (`overhang_tol_m`) for "zero overhang".
5. **Slot occupancy** = xy within `slot_occupancy_radius_m` of the slot
   center AND resting on the board (bottom within `resting_tol_m`).
   A required slot passes iff a required item occupies it passing all
   five criteria AND no wrong-category item occupies it (`wrong_slot`).
   This same rule realizes RS-9's "origin slots not newly wrong".
6. **S1 counter semantics (RS-7).** On-counter = xy within the counter
   footprint (+`margin_m`) and resting on the counter top. A NON-ordered
   product on the counter is an immediate `extra_item` failure at any t;
   an extra copy of an ORDERED product is recoverable — not success,
   `ongoing` until fixed or timeout. Quantities must match exactly.
7. **Timeout gates success** (mirrors the desk judge): at
   `t >= timeout_s` the verdict is fail with `timeout` appended to the
   collected penalties — a late completion never scores.
8. **Spawn poses are single-sourced.** `store.spawn_pose(plano, item)`
   computes every item's initial pose; `build_store` spawns from it and
   `build_retail_cfg` uses it for the verifier's home reference — the
   scene and the verifier cannot drift.
9. **Deferred:** the HAR-1 rollout `--tier S1|S2|S3` flag lands with T15
   (the S1 expert graph), where a retail episode can actually roll out
   end-to-end; RS-6's scoring record shape ships here (`score_episode`).

## Consequences

- `verifier/placement.toml` owns every threshold (RS-4); nothing inline.
- The retail failure classes extend the VER-3 taxonomy:
  `misplaced, misaligned, overhang, wrong_slot, missing_item, extra_item`
  (+ `timeout` reused).
- Capacity > 1 occupancy counting is deferred with ADR-15's stocking v0.
