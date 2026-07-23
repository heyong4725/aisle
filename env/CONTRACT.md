# Driver topic contract (env/CONTRACT.md)

The **driver topic contract** for the sim bridge (`src/aisle/nodes/dora_genesis.py`).
Phase 4 hardware honors THIS table, not the code: a real robot swaps the
bridge node behind the same topics, schemas, rates, units, and frames.

Schemas are the closed CAP-2 vocabulary (`registry/schema/schemas.toml`);
shapes reference SPEC 010 §2 dimension symbols (`h`, `w`, `n_dof`, `n_obj`,
`n_scan`). Every message carries TC-2 metadata: `sim_time_ns`, `env_id`,
`seq`. Base topics (`base_*`, `frame_info`) exist only for the `mobile`
embodiment (SPEC 210); fixed-base graphs never wire them.

## Inputs (commands into the bridge)

| Topic         | Schema             | Shape   | Rate      | Units / meaning                         | Frame |
|---------------|--------------------|---------|-----------|-----------------------------------------|-------|
| `tick`        | `timer_tick`       | opaque  | 100 Hz    | dora timer; drives sim time. Opaque — consumers MUST NOT read the payload | —     |
| `joint_cmd`   | `jointvec_f32`     | n_dof   | ≤100 Hz   | joint position targets (rad)            | base  |
| `gripper_cmd` | `scalar_f32`       | 1       | ≤30 Hz    | 0.0 = open … 1.0 = closed               | —     |
| `base_cmd`    | `base_cmd2d_f32`   | 2       | ≤50 Hz    | diff-drive `(v m/s, omega rad/s)` (mobile) | base  |
| `reset`       | `reset_request_u32`| 2       | service   | `(seed, mode)`, mode 0=teleport 1=behavioral (TC-6). A dora SERVICE (request/reply via `request_id`), not a rated stream; the bridge MUST NOT publish observations between `reset` and `reset_done` | — |

## Outputs (state out of the bridge)

Rates are the exact SPEC 010 §2 contract rates (TC-4: producers within ±20%),
NOT the tick rate — a conformant hardware driver must honor these.

**Sim vs. hardware rate note.** These declared rates are the contract a real
driver honors. Genesis headless runs sub-realtime (~0.75x here, and the
mobile guard↔bridge keep-out feedback cycle adds latency), so a 50 Hz topic
cannot meet TC-4's ±20% *wall-clock* band under simulation. Per **TC-4**
(amended, PR #16): under simulation conformance is enforced against the
*sim-time* scheduler rate (±20%) and the wall-clock rate need only stay above
a 0.5x liveness floor; on real hardware the wall-clock ±20% band applies. The
live conformance test asserts both the sim-time rate and the wall liveness
floor accordingly.

| Topic           | Schema             | Shape     | Rate    | Units / meaning                          | Frame |
|-----------------|--------------------|-----------|---------|------------------------------------------|-------|
| `joint_state`   | `jointvec_f32`     | n_dof     | 100 Hz  | measured joint positions (rad); meta `names` | base  |
| `gripper_state` | `scalar_f32`       | 1         | 100 Hz  | 0.0 = open … 1.0 = closed                | —     |
| `oracle_state`  | `posearray7d_f32`  | n_obj*7   | 30 Hz   | privileged ground-truth pose (xyz+quat) per box, fixed order. Consumable only by verifier-* nodes (VAL-6) | store |
| `poses`         | `posearray7d_f32`  | n_obj*7   | 15 Hz   | non-privileged tier-T0 perception poses (xyz+quat) | store |
| `rgb_overhead`  | `rgb8_image`       | h*w*3     | 30 Hz   | overhead camera; meta `{h, w, enc:"rgb8"}` | store |
| `rgb_wrist`     | `rgb8_image`       | h*w*3     | 30 Hz   | wrist camera (attached to EE link); meta `{h, w, enc:"rgb8"}` | flange |
| `depth_overhead`| `depth_f32`        | h*w       | 15 Hz   | overhead depth (m; 0 = invalid)          | store |
| `reset_done`    | `reset_done_u32`   | 1         | on reset| acknowledges a `reset`; meta `seed, mode, t_reset_ms` (TC-6) | —     |
| `bridge_info`   | `json_utf8`        | 1         | best_effort | startup announce (dof count, genesis version, env_hash) | — |
| `base_pose`     | `base_pose3d_f32`  | 3         | 50 Hz   | base origin `(x, y, yaw)` (m, m, rad) (mobile) | store |
| `base_scan`     | `base_scan_f32`    | n_scan    | 10 Hz   | planar range ranges (m); meta `{angle_min, angle_max, n}` (mobile) | base |
| `frame_info`    | `json_utf8`        | 1         | once    | store/base frame conventions, at startup (mobile) | —     |

## §frames

MOB-5: all base topics live in a fixed **store frame** defined by the scene.
The bridge publishes `frame_info` **once at startup** to announce the
conventions; the transforms below never change during an episode.

- **store frame** (`store`): fixed, scene-defined world origin. `base_pose`,
  `oracle_state`, `poses`, and camera outputs are expressed here.
- **base frame** (`base`): rides the base origin. `base_pose = (x, y, yaw)`
  is the pose of the base origin expressed in the store frame. `base_cmd`
  `(v, omega)` and `base_scan` ranges are in the base frame.
- **base ↔ store transform**: a point `p_base` in the base frame maps to the
  store frame by a planar rotation then translation —
  `p_store = R(yaw) · p_base + (x, y)`, where `R(yaw)` is the 2-D rotation by
  the base yaw. The inverse is `p_base = R(-yaw) · (p_store - (x, y))`.
- **arm mount** (ADR-13): the arm root rides the base origin, so the arm's
  base frame **is** the mobile base frame; at pose `(0, 0, 0)` the base
  frame coincides with the store frame. Arm topics (`joint_*`, TC-5) are
  therefore unchanged from the fixed-base contract — expressed relative to
  the (now moving) arm base — and MOB-4 keeps a franka arm subtree valid
  unchanged under the mobile profile.
- **`base_scan` angles**: `n` ranges swept from `angle_min` to `angle_max`
  (base-relative, radians), each a planar ray distance in meters.

`frame_info` payload (emitted once): `{store_frame, base_frame, base_pose,
arm_mount}` — human-readable descriptions of the conventions above.
