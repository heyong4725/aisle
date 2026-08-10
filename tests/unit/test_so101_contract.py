import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from aisle.embodiment import (
    SO101_ARM_JOINTS,
    SO101_GRIPPER_JOINTS,
    SO101_JOINTS,
    from_wire_joint_order,
    profile_dof_indices,
    profile_joint_names,
    to_wire_joint_order,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = REPO_ROOT / "assets" / "so101"
URDF = ASSET_ROOT / "so101.urdf"
OFFICIAL_COMMIT = "7629d2ad9853d10fb903093a33ef6114099d97e5"
OFFICIAL_LIMITS = {
    "shoulder_pan": (-1.91986, 1.91986),
    "shoulder_lift": (-1.74533, 1.74533),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.65806, 1.65806),
    "wrist_roll": (-2.74385, 2.84121),
    "gripper": (-0.174533, 1.74533),
}


def test_tc5_official_so101_joint_contract():
    """TC-5: the official SO-101 follower exposes five arm joints followed
    by one gripper joint, in the exact wire order declared by the contract."""
    assert SO101_ARM_JOINTS == (
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
    )
    assert SO101_GRIPPER_JOINTS == ("gripper",)
    assert SO101_JOINTS == SO101_ARM_JOINTS + SO101_GRIPPER_JOINTS
    assert len(SO101_JOINTS) == 5 + 1


def test_tc5_bridge_maps_publish_and_command_order_by_name():
    """TC-5: the bridge resolves both joint_state and joint_cmd through the
    official wire names instead of trusting the simulator's parser order."""

    class Joint:
        n_dofs = 1

        def __init__(self, index):
            self.dofs_idx_local = [index]

    class Robot:
        n_dofs = 6

        def __init__(self):
            parser_order = tuple(reversed(SO101_JOINTS))
            self.by_name = {name: Joint(index) for index, name in enumerate(parser_order)}

        def get_joint(self, name):
            return self.by_name[name]

    profile = {
        "arm_joint_names": list(SO101_ARM_JOINTS),
        "gripper_joint_names": list(SO101_GRIPPER_JOINTS),
    }
    indices = profile_dof_indices(Robot(), profile)
    assert profile_joint_names(profile) == SO101_JOINTS
    assert indices == (5, 4, 3, 2, 1, 0)

    wire_command = np.arange(6, dtype=np.float32)
    native_command = from_wire_joint_order(wire_command, indices)
    assert native_command.tolist() == [5, 4, 3, 2, 1, 0]
    assert to_wire_joint_order(native_command, indices).tolist() == wire_command.tolist()


def test_scn4_official_asset_provenance_and_mesh_closure():
    """SCN-4, CON-5: the SO-101 asset is pinned to the owner-approved
    official commit and every relative mesh reference is vendored."""
    with open(ASSET_ROOT / "PROVENANCE.toml", "rb") as f:
        provenance = tomllib.load(f)
    assert provenance == {
        "source": {
            "repository": "https://github.com/TheRobotStudio/SO-ARM100",
            "commit": OFFICIAL_COMMIT,
            "path": "Simulation/SO101/so101_new_calib.urdf",
            "license": "Apache-2.0",
        }
    }
    assert (ASSET_ROOT / "LICENSE").is_file()
    root = ET.parse(URDF).getroot()
    meshes = {node.attrib["filename"] for node in root.findall(".//mesh")}
    assert meshes
    assert all(not Path(mesh).is_absolute() and (ASSET_ROOT / mesh).is_file() for mesh in meshes)


def test_tc5_scn4_official_urdf_joint_names_and_limits():
    """TC-5, SCN-4: the vendored URDF retains the official 5+1 actuated
    joint names, order, position limits, axes, and velocity parameters."""
    root = ET.parse(URDF).getroot()
    joints = {
        joint.attrib["name"]: joint
        for joint in root.findall("joint")
        if joint.attrib["type"] != "fixed"
    }
    motor_order = []
    for transmission in root.findall("transmission"):
        actuator = transmission.find("actuator")
        joint = transmission.find("joint")
        assert actuator is not None and joint is not None
        motor_order.append(
            (int(actuator.attrib["name"].removeprefix("motor")), joint.attrib["name"])
        )
    assert tuple(name for _, name in sorted(motor_order)) == SO101_JOINTS
    assert set(joints) == set(SO101_JOINTS)
    for name, expected_limits in OFFICIAL_LIMITS.items():
        joint = joints[name]
        limit = joint.find("limit")
        axis = joint.find("axis")
        assert axis is not None and axis.attrib["xyz"] == "0 0 1"
        assert limit is not None
        assert (float(limit.attrib["lower"]), float(limit.attrib["upper"])) == expected_limits
        assert float(limit.attrib["velocity"]) == 10.0
