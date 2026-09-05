"""The frozen primitive API a monolithic orchestration module may call (SPEC
440, MON-3/MON-4/MON-5).

One `Primitives` instance is built by the trusted broker
(aisle.nodes.monolith_broker) from the same pinned physics/meds/embodiment
profile the typed graph's nodes load, and every method below delegates to
the SAME function object the typed node calls: the L1 rung's
`segmented_pose.L1Session`, the planner's `grasp_topdown.plan_grasp`, and
the executor's `ik_trajectory.StagedPlan` / `StageStreamer`. Nothing here
re-implements a primitive, so the two arms cannot drift apart in robot
capability (MON-4 "same pinned robot-primitive implementations").

The API is versioned by `API_VERSION`; its documented surface is the set of
names in `PUBLIC_API`, and `describe()` renders that surface for the
monolithic-arm documentation without exposing manifests, graph YAML, or
validator entry points (MON-3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aisle.nodes import grasp_topdown, ik_trajectory, segmented_pose
from aisle.scenes.pharmacy import (
    MED_NAMES,
    load_meds,
    load_physics,
    resolve_layout,
)

API_VERSION = "1.0"

#: the complete agent-visible surface of a `Primitives` object; anything
#: else on the instance is broker-private and pinned absent by test (MON-3)
PUBLIC_API = (
    "api_version",
    "embodiment",
    "med_names",
    "meds",
    "physics",
    "home",
    "layout",
    "pose_session",
    "plan_grasp",
    "staged_plan",
    "streamer",
    "describe",
)


ATTRIBUTE_DOCS = {
    "api_version": "The primitive API version this broker speaks.",
    "embodiment": "The embodiment profile the run uses (franka or so101).",
    "meds": "Med name -> spec dict (size = box x, y, z in metres, ...).",
    "physics": "The public physics/scene profile (physics.toml, SCN-2) both arms read.",
}


@dataclass(frozen=True)
class GraspPlan:
    """What the typed graph carries as the `grasp_pose` message plus its
    metadata (approach_m, front, place_tcp_z) — one value, same fields."""

    grasp: tuple  # 7 floats: xyz + wxyz quaternion, base frame
    approach_m: float
    place_tcp_z: float
    front: bool


@dataclass
class Primitives:
    embodiment: str
    physics: dict = field(repr=False)
    meds: dict = field(repr=False)
    api_version: str = API_VERSION

    @classmethod
    def _load(cls, embodiment: str = "franka") -> Primitives:
        """Broker-side constructor (not part of the module-visible API)."""
        return cls(embodiment=embodiment, physics=load_physics(), meds=load_meds())

    # -- observations the typed graph derives from the same config -------

    @property
    def med_names(self) -> tuple[str, ...]:
        """The five med names in scene order."""
        return tuple(MED_NAMES)

    @property
    def home(self) -> np.ndarray:
        """The embodiment's home posture (full dof vector), as ik-trajectory
        loads it."""
        return np.asarray(
            self.physics["embodiment"][self.embodiment]["home_qpos"], dtype=np.float32
        )

    @property
    def layout(self) -> dict:
        """Public scene geometry (SCN-2): shelf/tray placement — what the
        planner and executor read from physics.toml."""
        return resolve_layout(self.physics, self.embodiment)

    # -- the three pinned primitives -------------------------------------

    def pose_session(self) -> segmented_pose.L1Session:
        """The L1 pose estimator, byte-for-byte the object segmented-pose
        runs: feed `on_bridge_info`, `on_target_request`, `on_seg`,
        `on_depth`, `on_reset_done`; a paired same-stamp frame yields the
        estimate dict (pos, target_med, neighbours, ...) or raises
        `segmented_pose.PoseRefused` (TC-9)."""
        from aisle.verifier.stages import backproject_overhead

        return segmented_pose.L1Session(
            meds=self.meds,
            backprojector=lambda calibration: (
                lambda depth, pixels: backproject_overhead(depth, calibration, pixels)
            ),
        )

    def plan_grasp(self, pose, target_med: str, neighbours: list | None = None) -> GraspPlan:
        """grasp-planner-topdown's decision for one estimate: the same
        `plan_grasp` call with the same physics-profile arguments."""
        if target_med not in self.meds:
            raise ValueError(f"unknown med {target_med!r}")
        profile = self.physics["embodiment"][self.embodiment]
        layout = self.layout
        shelf = layout["shelf"]
        tray = layout["tray"]
        flat = np.asarray(pose, dtype=np.float64).reshape(-1)
        front = bool(profile.get("force_front_grasp", False)) or grasp_topdown.needs_front(
            float(flat[0]), float(flat[2]), shelf
        )
        constraints = None
        if neighbours is not None:
            constraints = grasp_topdown.neighbour_constraints(
                list(neighbours), target_med, list(MED_NAMES), self.meds
            )
        grasp, approach, place_z = grasp_topdown.plan_grasp(
            flat[:7] if flat.shape[0] >= 7 else flat,
            self.meds[target_med]["size"],
            front=front,
            shelf_front_x=shelf["pos"][0] - shelf["level_size"][0] / 2,
            tray_top_z=tray["pos"][2] + tray["size"][2] / 2,
            neighbours=constraints,
            finger_open=float(profile["gripper_open_m"]),
            finger_clear=float(profile["gripper_finger_clear_m"]),
            topdown_approach=float(
                profile.get("pregrasp_height_m", self.physics["ik"]["pregrasp_height_m"])
            ),
            radial_front=bool(profile.get("force_front_grasp", False)),
            front_clearance=float(profile.get("front_clearance_m", grasp_topdown.FRONT_CLEARANCE)),
            front_tcp_overshoot=float(profile.get("front_tcp_overshoot_m", 0.0)),
            front_jaw_center_offset=float(profile.get("front_jaw_center_offset_m", 0.0)),
            front_vertical_offset=float(profile.get("front_vertical_offset_m", 0.0)),
        )
        return GraspPlan(
            grasp=tuple(float(v) for v in np.asarray(grasp).reshape(-1)[:7]),
            approach_m=float(approach),
            place_tcp_z=float(place_z),
            front=front,
        )

    def staged_plan(self, plan: GraspPlan) -> ik_trajectory.StagedPlan:
        """ik-trajectory's waypoint solve for a grasp: the same `StagedPlan`
        with the tray destination and home seed the executor uses. Check
        `.ok` / `.error` before streaming."""
        tray_pos = self.layout["tray"]["pos"]
        return ik_trajectory.StagedPlan(
            np.asarray(plan.grasp, dtype=np.float32),
            (float(tray_pos[0]), float(tray_pos[1])),
            float(plan.approach_m),
            self.home,
            place_z=float(plan.place_tcp_z),
            embodiment=self.embodiment,
        )

    def streamer(self, staged: ik_trajectory.StagedPlan, max_vel: float = 1.0):
        """ik-trajectory's executor: `.step(qpos) -> (joint_cmd | None,
        gripper_cmd | None, logs)` at the joint_state cadence (TC-4,
        dt=0.01), `.done` once the plan finished."""
        return ik_trajectory.StageStreamer(
            list(staged.stages), self.home, 0.01, float(max_vel), embodiment=self.embodiment
        )

    def describe(self) -> dict[str, Any]:
        """The documented surface (MON-3): names and docstrings only."""
        members = {}
        for name in PUBLIC_API:
            if name in ATTRIBUTE_DOCS:
                members[name] = ATTRIBUTE_DOCS[name]
            else:
                members[name] = (getattr(type(self), name).__doc__ or "").strip()
        return {"api_version": self.api_version, "embodiment": self.embodiment, "members": members}


#: the pinned implementations both arms share, by import path — the MON-4
#: interface map and the MON-5 test pin these to be the SAME objects the
#: typed nodes call
PINNED_IMPLEMENTATIONS = {
    "pose": "aisle.nodes.segmented_pose.L1Session",
    "grasp": "aisle.nodes.grasp_topdown.plan_grasp",
    "trajectory": "aisle.nodes.ik_trajectory.StagedPlan",
    "executor": "aisle.nodes.ik_trajectory.StageStreamer",
}


def api_document() -> str:
    """JSON rendering of the primitive surface for the monolithic docs."""
    return json.dumps(
        {
            "api_version": API_VERSION,
            "public_api": list(PUBLIC_API),
            "pinned_implementations": PINNED_IMPLEMENTATIONS,
        },
        indent=2,
        sort_keys=True,
    )
