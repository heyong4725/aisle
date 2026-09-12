"""Controller-owned public observation projection for the paired T1 L2 pilot.

Explicit fields at every nesting level prevent a bridge schema extension from
implicitly granting policy access to simulator identity. This is a data boundary,
not an isolation attestation: graph routing and episode lifecycle must also bind
this projection before collecting a pilot.
"""

from __future__ import annotations

import math

_REFUSAL = "invalid public pilot observation"


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(_REFUSAL)
    return value


def _vector(value, length):
    if type(value) is not list or len(value) != length:
        raise ValueError(_REFUSAL)
    return [_number(item) for item in value]


def _camera(value, transform):
    resolution = _vector(value["resolution"], 2)
    if any(type(item) is not int or item <= 0 for item in resolution):
        raise ValueError(_REFUSAL)
    intrinsics = {name: _number(value["intrinsics"][name]) for name in ("fx", "fy", "cx", "cy")}
    fov = _number(value["fov_deg"])
    if not 0 < fov < 180 or intrinsics["fx"] <= 0 or intrinsics["fy"] <= 0:
        raise ValueError(_REFUSAL)
    return {
        "resolution": resolution,
        "fov_deg": fov,
        "intrinsics": intrinsics,
        transform: {
            "pos": _vector(value[transform]["pos"], 3),
            "quat_xyzw": _vector(value[transform]["quat_xyzw"], 4),
        },
    }


def public_bridge_info(info: dict) -> dict:
    """BND-2/MON-4: copy only the L2 sensor calibration schema, recursively."""
    try:
        block = info["calibration"]
        if info["perception"] != "L2" or type(block["calibration_version"]) is not int:
            raise ValueError(_REFUSAL)
        if block["calibration_version"] != 1:
            raise ValueError(_REFUSAL)
        overhead = _camera(block["overhead"], "cam_to_base")
        overhead["depth_scale_m"] = _number(block["overhead"]["depth_scale_m"])
        if overhead["depth_scale_m"] <= 0:
            raise ValueError(_REFUSAL)
        return {
            "perception": "L2",
            "calibration": {
                "calibration_version": 1,
                "overhead": overhead,
                "wrist": _camera(block["wrist"], "cam_to_ee"),
            },
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError(_REFUSAL) from None


def public_goal(goal: dict) -> dict:
    """BND-2: the selected T1 task has no seed or privileged reset state."""
    try:
        target = goal["target_med"]
        timeout = _number(goal["timeout_s"])
        if type(target) is not str or not target or goal["tier"] != "T1" or timeout <= 0:
            raise ValueError(_REFUSAL)
        return {"target_med": target, "tier": "T1", "timeout_s": timeout}
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError(_REFUSAL) from None


def public_metadata(metadata: dict) -> dict:
    """BND-2: never relay reset request ids (which embed seeds) or extra metadata."""
    result = {}
    if "goal_id" in metadata:
        goal_id = metadata["goal_id"]
        if type(goal_id) is not str:
            raise ValueError(_REFUSAL)
        result["goal_id"] = goal_id
    if "sim_time_ns" in metadata:
        stamp = metadata["sim_time_ns"]
        if type(stamp) is not int or stamp < 0:
            raise ValueError(_REFUSAL)
        result["sim_time_ns"] = stamp
    return result


def main() -> None:
    """Project trusted controller topics onto the registered public policy schema.

    The turn wrapper supplies fresh transport sequence/turn metadata. Incoming
    request ids and extension fields are never copied. Reset is a notification,
    not the reset service's private reply payload. No oracle topic is forwarded.
    """
    import json
    import os

    import pyarrow as pa

    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)
    projections = {"bridge_info": public_bridge_info, "episode_goal": public_goal}
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if not env_accepts(metadata, env_pin):
            continue
        topic = event["id"]
        if topic in projections:
            try:
                payload = projections[topic](json.loads(event["value"][0].as_py()))
            except (IndexError, TypeError, ValueError):
                raise ValueError(_REFUSAL) from None
            send(topic, pa.array([json.dumps(payload)]), public_metadata(metadata))
        elif topic == "reset_done":
            send(topic, pa.array([1], type=pa.uint32()), public_metadata(metadata))


if __name__ == "__main__":
    main()
