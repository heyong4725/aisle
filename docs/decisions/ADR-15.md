# ADR-15 — Store scene design: planogram vocabulary, geometry, stocking (T12, SPEC 200)

Status: accepted (CON-15: RS-1..3 fix the artifacts but not the product
vocabulary, geometry source, or stocking model; the agent picks and
records). Task: T12. Specs: 200 (RS-1..3), extends 020/210. Relates to
[[ADR-13]] (kinematic base), [[ADR-14]] (mobile guard limits).

## Decisions

1. **Products = the five meds.** `meds.toml` stays the product/category
   vocabulary (SCN-2); planogram categories are med names. S1's "spec"
   disambiguator is DERIVED deterministically from `meds.toml` (color word +
   size in mm) — no second product file to drift.
2. **Store geometry lives in `planogram.toml`.** RS-1 makes the planogram
   the single source of truth the scene is GENERATED from and verifiers
   query; the store section ([store], [units.*]) carries unit world poses,
   unit geometry, counter, bin, and aisle definitions. `physics.toml`
   keeps what it already owns (sim, materials, cameras, embodiment).
3. **Slot ids** are `"<unit>-L<level>-S<index>"` (e.g. `A1-L0-S0`).
   Planogram FILE ORDER is the item/oracle order (SCN-1 analog).
4. **Slot template frame.** `template_pose` is 7d `[x,y,z,qx,qy,qz,qw]`
   (TC-1 quat order) in the UNIT frame: origin at the unit's floor-center,
   +x facing the aisle; z is the BOARD SURFACE (item base), so item center
   spawns at `z + size_z/2`. World pose = unit yaw/translation ∘ template.
5. **Stocking model.** Initial shelf stock = `capacity` items per slot
   (capacity 1 in v0); the restocking bin always holds ONE item of every
   category (`bin#<category>`), so any S2 de-stock is restockable. Every
   category has ≥3 shelf slots, so an S1 qty of 1..3 is always fulfillable.
6. **Layout aligned to `locations.toml`** (MOB-2 named targets):
   `shelf_zone_A/B` keep their T11 poses; `counter`/`bin` move to the real
   counter/bin (both in the frozen dir, one PR). Two shelf rows (aisle_A:
   units A1, A2 at y=+1.6; aisle_B: B1 at y=-1.6) leave a corridor ≥0.9 m
   (RS-2) with the counter/bin area open at x<0.
7. **No global reach assert.** The pharmacy build's SCN-3 reachability
   assert is meaningless for a store: reach is relative to the MOBILE base,
   which navigates. Slot reachability is a per-episode property (base
   adjacent to the unit), not a build-time one.

## Consequences

- The retail verifier (T13) queries the same `planogram.toml` (slot →
  category/template/capacity) — no scene introspection needed.
- Adding a unit/slot = editing one file; the scene, generators, and
  verifiers follow automatically.
- `capacity > 1` in-slot arrangement (a depth row behind the template) is
  deferred until a scenario needs it; the field is honored but v0 keeps
  capacity 1.
