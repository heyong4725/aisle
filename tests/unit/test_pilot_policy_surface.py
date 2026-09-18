"""BND-2/BND-3/MON-4: public pilot inputs exclude private controller fields."""

import copy
import json

import pytest

from aisle.harness import pilot_policy_surface as surface

pytestmark = pytest.mark.unit


def calibration():
    camera = {
        "resolution": [640, 480],
        "fov_deg": 60.0,
        "intrinsics": {"fx": 400.0, "fy": 400.0, "cx": 319.5, "cy": 239.5},
    }
    pose = {"pos": [0.0, 0.0, 1.0], "quat_xyzw": [0.0, 0.0, 0.0, 1.0]}
    return {
        "calibration_version": 1,
        "overhead": {**copy.deepcopy(camera), "cam_to_base": pose, "depth_scale_m": 1.0},
        "wrist": {**copy.deepcopy(camera), "cam_to_ee": copy.deepcopy(pose)},
    }


def test_bridge_projection_removes_private_fields_at_every_object_level():
    """BND-2: future nested announcement fields cannot enter policy calibration."""
    expected = calibration()
    private = copy.deepcopy(expected)

    def taint(value):
        if isinstance(value, dict):
            for child in list(value.values()):
                taint(child)
            value["private_truth"] = {"seed": 981, "object_ids": [4, 5]}

    taint(private)
    announcement = {
        "calibration": private,
        "perception": "L2",
        "segmentation_ids": {"ibuprofen": [9]},
        "env_hash": "private-context",
    }
    projected = surface.public_bridge_info(announcement)
    assert projected == {"perception": "L2", "calibration": expected}
    projected["calibration"]["overhead"]["resolution"][0] = 1
    assert private["overhead"]["resolution"] == [640, 480]


def test_goal_and_metadata_do_not_disclose_seed_or_request_id():
    """BND-2/BND-3: retain only public T1 task fields and explicit transport stamps."""
    assert surface.public_goal(
        {
            "target_med": "ibuprofen",
            "tier": "T1",
            "timeout_s": 30.0,
            "seed": 981,
            "reset_sim_ns": 10,
            "oracle": {"success": True},
        }
    ) == {"target_med": "ibuprofen", "tier": "T1", "timeout_s": 30.0}
    assert surface.public_metadata(
        {
            "goal_id": "ep-0000",
            "sim_time_ns": 10,
            "request_id": "reset-0-981",
            "seed": 981,
            "env_hash": "secret",
        }
    ) == {"goal_id": "ep-0000", "sim_time_ns": 10}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "400", {"seed": 981}])
def test_invalid_public_leaf_refuses_without_echoing_private_input(bad):
    """BND-2: nested payloads and malformed calibration fail without input disclosure."""
    block = calibration()
    block["overhead"]["intrinsics"]["fx"] = bad
    with pytest.raises(ValueError, match="^invalid public pilot observation$"):
        surface.public_bridge_info({"perception": "L2", "calibration": block})


@pytest.mark.parametrize(
    "change", [{"tier": "T2"}, {"timeout_s": -1}, {"target_med": {"seed": 981}}]
)
def test_goal_refuses_unsupported_surface(change):
    """MON-4/BND-2: projection cannot silently reinterpret another task surface."""
    with pytest.raises(ValueError, match="^invalid public pilot observation$"):
        surface.public_goal({"target_med": "ibuprofen", "tier": "T1", "timeout_s": 30, **change})


def test_node_projects_real_arrow_events_and_never_relays_verdict(monkeypatch):
    """BND-2/BND-3/MON-4: the executable boundary strips payload AND metadata leaks."""
    import pyarrow as pa

    import aisle.turn_node

    def event(topic, value):
        return {
            "type": "INPUT",
            "id": topic,
            "value": value,
            "metadata": {"request_id": "reset-0-981", "seed": 981, "sim_time_ns": 10},
        }

    class Node:
        sent = []

        def __iter__(self):
            return iter(
                [
                    event(
                        "bridge_info",
                        pa.array(
                            [
                                json.dumps(
                                    {"perception": "L2", "calibration": calibration(), "seed": 981}
                                )
                            ]
                        ),
                    ),
                    event(
                        "episode_goal",
                        pa.array(
                            [
                                json.dumps(
                                    {
                                        "target_med": "ibuprofen",
                                        "tier": "T1",
                                        "timeout_s": 30,
                                        "seed": 981,
                                    }
                                )
                            ]
                        ),
                    ),
                    event("reset_done", pa.array([981], type=pa.uint32())),
                    event("episode_result", pa.array(['{"success": true, "seed": 981}'])),
                    event("turn", pa.array([0])),
                ]
            )

        def send_output(self, topic, value, metadata):
            self.sent.append((topic, value.to_pylist(), metadata))

    node = Node()
    monkeypatch.setattr(aisle.turn_node, "Node", lambda: node)
    monkeypatch.delenv("AISLE_ENV_PIN", raising=False)
    surface.main()
    assert [item[0] for item in node.sent] == ["bridge_info", "episode_goal", "reset_done"]
    assert node.sent[-1][1] == [1]
    assert all(metadata["sim_time_ns"] == 10 for _, _, metadata in node.sent)
    assert "981" not in json.dumps(node.sent)
    assert "success" not in json.dumps(node.sent)
    assert json.loads(node.sent[0][1][0])["calibration"] == calibration()
