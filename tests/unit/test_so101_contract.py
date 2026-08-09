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
