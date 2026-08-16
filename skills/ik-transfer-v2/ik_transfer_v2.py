"""ik-transfer-v2: ik-trajectory with a shelf-clearing routed transfer.

Campaign A3/arm-F idea I4. Root cause (run 20260814-235921-b5176c, seed
33, `collision`): the stock transfer is ONE straight Cartesian segment
from above the grasp to the tray, so it starts descending while still
sweeping over the shelf; the carried box's bottom crossed a same-level
neighbour's top with ~5 mm margin and clipped it (traces: neighbour
displacement onset t=12.07 s, exactly as the transfer's z sagged through
the neighbour's top plane). At constant retract height the carried box
bottom sits at board_top + approach + lift - grip_engagement, ~5.5 cm
above the tallest med (0.110), so the fix is routing, not speed:

  rise over the grasp to clear_z -> traverse at CONSTANT clear_z until
  past the shelf's y-extent -> descend to the stock transfer pose.

Everything else (stages, executor, read ladder, retry loop) is the
proven ik-trajectory implementation, reused by subclassing StagedPlan
and rebuilding only the `transfer` (and matching `clear`) stage. Front
grasps and so101 keep the stock plan untouched; any IK failure on the
routed path falls back to the stock straight transfer (never worse).
"""

from __future__ import annotations

import sys

import numpy as np

import aisle.nodes.ik_trajectory as ik

# past the shelf's y half-span (0.33) with margin, shy of the tray centre
SHELF_EXIT_Y = -0.40
# extra height over the retract level, tried first; 0.0 = flat traverse
CLEAR_EXTRA_M = (0.04, 0.0)


class RoutedPlan(ik.StagedPlan):
    def __init__(
        self,
        grasp_pose: np.ndarray,
        tray_xy,
        approach_m: float,
        q_seed: np.ndarray,
        place_z: float = ik.PLACE_TCP_Z,
        embodiment: str = "franka",
    ) -> None:
        super().__init__(grasp_pose, tray_xy, approach_m, q_seed, place_z, embodiment)
        if not self.ok or not self.stages or embodiment != "franka":
            return
        from aisle.scenes.pharmacy import load_physics

        pose = np.asarray(grasp_pose, dtype=np.float32).reshape(7)
        grasp_pos = pose[:3].astype(np.float64)
        approach_axis = ik.quat_to_rotation(pose[3:7])[:, 2]
        if abs(float(approach_axis[2])) < 0.5:
            return  # front grasp: stock plan already retracts clear of the shelf
        profile = load_physics()["embodiment"][embodiment]
        lift = float(profile.get("trajectory_lift_m", ik.LIFT_H))
        transfer_h = float(profile.get("trajectory_transfer_z", ik.TRANSFER_TCP_Z))
        transfer_vel = float(profile.get("trajectory_transfer_vel_scale", 0.35))
        grasp_cmd = float(profile.get("gripper_grasp_cmd", 1.0))
        start = grasp_pos - approach_axis * approach_m + np.array([0.0, 0.0, lift])
        place_rot = ik.topdown_rotation(0.0)
        transfer_idx = next(i for i, s in enumerate(self.stages) if s.name == "transfer")
        clear_idx = next(i for i, s in enumerate(self.stages) if s.name == "clear")
        seed = self.stages[transfer_idx - 1].q  # retract end
        transfer_pos = np.array([tray_xy[0], tray_xy[1], transfer_h])
        for extra in CLEAR_EXTRA_M:
            clear_z = float(start[2]) + extra
            route = (
                np.array([start[0], start[1], clear_z]),
                np.array([tray_xy[0], SHELF_EXIT_Y, clear_z]),
                transfer_pos,
            )
            path: list | None = []
            p, q = start, seed
            for wp in route:
                seg = ik.ik_continuation(p, wp, place_rot, q, embodiment=embodiment)
                if seg is None:
                    path = None
                    break
                path.extend(seg)
                p, q = wp, seg[-1]
            if path is not None:
                self.stages[transfer_idx] = ik.Stage(
                    "transfer", tuple(path), grasp_cmd, 0.3, vel=transfer_vel
                )
                self.stages[clear_idx] = ik.Stage("clear", (path[-1],), 0.0, 0.1)
                print(f"routed transfer: clear_z {clear_z:.3f}", file=sys.stderr)
                return
        print("routed transfer IK failed; stock straight transfer kept", file=sys.stderr)


def main() -> None:
    ik.StagedPlan = RoutedPlan  # ik.main() resolves StagedPlan at call time
    ik.main()


if __name__ == "__main__":
    main()
