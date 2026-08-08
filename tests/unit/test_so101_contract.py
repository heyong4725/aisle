import pytest

from aisle.embodiment import SO101_ARM_JOINTS, SO101_GRIPPER_JOINTS, SO101_JOINTS

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
