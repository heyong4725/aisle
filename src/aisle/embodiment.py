"""Stable embodiment-level topic contracts (SPEC 010, TC-5)."""

SO101_ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
SO101_GRIPPER_JOINTS = ("gripper",)
SO101_JOINTS = SO101_ARM_JOINTS + SO101_GRIPPER_JOINTS
