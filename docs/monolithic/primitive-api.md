# Monolithic arm: module contract and primitive API (v1.0)

You write **one Python file**. The harness loads it, constructs your
`Controller`, feeds it observations, and executes the actions you return.
You never touch the simulator, the safety guard, the verifier, or the
harness; the same primitives the reference system uses are handed to you
as a `primitives` object.

## The module

```python
API_VERSION = "1.0"

class Controller:
    def __init__(self, primitives, log):
        ...                       # keep `primitives`; `log(str)` writes a line to the run log

    def on_event(self, event) -> list:
        """event = {"name", "sim_time_ns", "payload", "goal_id"}; return a list of actions."""
```

Only the standard library's pure-Python modules and `numpy` are importable.
Process, socket, network, file, loader, and harness modules are refused
and end the run as infrastructure-invalid. There is no other file: helper
modules, data files, and packages are not part of the deliverable.

Run it with:

```
uv run harness monolith check --module my_controller.py          # compile + construct, no sim
uv run harness monolith run --module my_controller.py --tier T1 --episodes 8 --seeds 0..7
```

The launcher currently supports only T1. Other tiers return a structured
`unsupported_monolithic_tier` failure before loading the module or starting a
rollout; they require their own declared observation and task setup.

`check` reports Python's own syntax, import, and runtime errors and nothing
else. `run` prints one JSON report and writes `runs/<id>/episodes.jsonl`.

## Observations (`event["name"]` / `event["payload"]`)

| name | payload | when |
|---|---|---|
| `bridge_info` | dict: `calibration`, `segmentation_ids` (med name -> ids), scene facts | at start and on republish |
| `seg_overhead` | `int32[h, w]` instance mask of the overhead camera | ~15 Hz |
| `depth_overhead` | `float32[h, w]` metres, same stamp as the mask it pairs with | ~15 Hz |
| `joint_state` | `float32[n_dof]` measured joints (arm + gripper dofs) | 100 Hz |
| `episode_goal` | dict: `target_med`, `tier`, ... ; `event["goal_id"]` set | once per episode |
| `episode_result` | dict verdict from the evaluator | closes the episode |
| `violation` | dict from the guard (a clamped or refused command) | as it happens |
| `reset_done` | `None`; the scene was reset, drop all episode state | between episodes |
| `tick` | int, seconds since the goal (1 Hz on the simulation clock) | 1 Hz |

## Actions (each list element is a one-key dict)

| action | payload | effect |
|---|---|---|
| `{"joint_cmd": seq}` | `n_dof` finite floats (full dof vector, gripper dofs included) | target joints; clamped by the guard's limits and velocity budget |
| `{"gripper_cmd": x}` | finite float (0 open ... 1 closed, embodiment mapping) | gripper target |
| `{"feedback": obj}` | JSON object; the harness expects `t`, `phase`, `retries` at every tick | progress reported to the run log |

## `primitives`

| member | meaning |
|---|---|
| `api_version` | `"1.0"` |
| `embodiment` | `"franka"` or `"so101"` |
| `med_names` | the five med names in scene order |
| `meds` | med name -> spec dict (`size` = box x, y, z in metres, ...) |
| `physics` | the public physics/scene profile (physics.toml) |
| `home` | `float32[n_dof]` home posture |
| `layout` | public scene geometry: `shelf`, `tray` (`pos`, `size`, `level_size`, `level_heights`...) |
| `pose_session()` | a pose estimator session: `on_bridge_info(d)`, `on_target_request({"target_med": m}) -> bool`, `on_seg(t, seg)`, `on_depth(t, depth)` (each returns an estimate dict `{pos, target_med, neighbours, ...}` once a same-stamp pair is available, else `None`; raises when the mask cannot support a pose), `on_reset_done()` |
| `plan_grasp(pose7, target_med, neighbours=None) -> GraspPlan` | grasp pose (`grasp`), `approach_m`, `place_tcp_z`, `front` for an estimated box |
| `staged_plan(grasp_plan)` | solves the pick-place waypoints; `.ok`, `.error`, `.stages` |
| `streamer(staged, max_vel=1.0)` | executor: `.step(qpos) -> (joint_cmd or None, gripper_cmd or None, log lines)` at the joint_state cadence; `.done` |
| `describe()` | this table as JSON |

Budgets: an episode times out at the harness-declared limit (60 s at T1);
seeds and reset behaviour are chosen by the harness; the evaluator is
hidden from you. There is no validator for this file: what Python accepts
runs.
