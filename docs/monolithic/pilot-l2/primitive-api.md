# Monolithic T1 L2 pilot primitive API

The module exports `API_VERSION = "1.0"` and `Controller(primitives, log)`.
`on_event(event)` returns actions through the existing broker. Public events,
actions and their task authority are enumerated in `interface-map.json`.

Create perception with `primitives.l2_pose_session()`. Its data-only handle has
`on_bridge_info(info)`, `on_target_request(request)`, `on_rgb(sim_time_ns, rgb)`,
`on_depth(sim_time_ns, depth)` and `on_reset_done()`. RGB is uint8 `(h,w,3)`;
depth is float32 metres `(h,w)`. Use same-stamp frames. Identity, confidence,
refusal thresholds and geometry are the shared pinned L2 implementation.
The handle exposes no segmentation operation or model object.

For a returned estimate, use `plan_grasp(pose, target_med, neighbours)`,
`staged_plan(grasp)`, and `streamer(staged_plan)` from the existing primitive
API. The streamer consumes measured joint state; returned joint/gripper actions
pass through the shared budget guard. There is no simulator, reset or evaluator
handle. No oracle verdict event is provided to terminate or retry a plan.

Only the public projection's camera calibration and T1 goal fields are available.
Reset notifications clear local perception/planning state. The controller owns
seed selection, fixed deadlines and result capture. Module code cannot change
those values or read private task seeds, oracle state or retained evidence.
