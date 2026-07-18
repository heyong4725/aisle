# SPEC 210 — Mobility contract extension (mobile profile)

Status: DRAFT, post-M0. Class C once stable (extends SPEC 010).
Adds a `mobile` embodiment profile: differential-drive base + arm. Fixed-base
profiles are untouched; every SPEC 010 topic and rule still applies.

- MOB-1: New topics — `base_pose` out Float32[3] (x, y, yaw; store frame) @50 Hz; `base_cmd` in Float32[2] (v m/s, omega rad/s) ≤50 Hz; `base_scan` out Float32[n] (planar ranges) @10 Hz, meta {angle_min, angle_max, n}. TC-2 metadata rules apply.
- MOB-2: Navigation is a dora ACTION (goal_id pattern, mirrors TC-7): goal `nav_goal` JSON {location: str | pose:[x,y,yaw]} where named locations resolve via `scenes/locations.toml` (counter, bin, shelf_zone_A...); feedback {t, dist_remaining} ≥2 Hz; result {status: success|fail, failure: null|blocked|timeout, t_end}.
- MOB-3: Budget guard extension (SPEC 080): base velocity limits, keep-out zones from `env/limits.toml` (e.g., min distance to shelves while arm is extended), and a mutual-exclusion rule — arm motion class and base motion above v_creep MUST NOT be commanded simultaneously (clamp base to v_creep, emit violation).
- MOB-4: The `mobile` profile's arm topics are identical to the fixed-base contract (TC-5); a graph valid for `franka` desk tasks MUST validate unchanged for the mobile profile's arm subtree (embodiment field extended, VAL EMBODIMENT_MISMATCH covers base-requiring nodes on fixed-base graphs).
- MOB-5: Store frame: all base topics in a fixed store frame defined by the scene; the bridge publishes `frame_info` once at startup (transform base-frame ↔ store-frame conventions documented in CONTRACT.md §frames).

Acceptance: tests/accept/test_contract_mobile.py::test_mobile_schema_conformance (MOB-1, mirrors TC-A1), ::test_nav_action_lifecycle (MOB-2), tests/graph/test_guard_mutex.py::test_arm_base_exclusion (MOB-3), tests/unit/test_validator_mobile.py (MOB-4).
