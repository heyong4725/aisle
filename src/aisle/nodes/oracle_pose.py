"""oracle-pose node (CAP-5): tier-T0 perception passthrough.

Selects the requested med's 7-float pose block from the non-privileged
`poses` topic (SPEC 010, issue #2 resolution) and republishes it as
`target_pose`. Ladder rung L0: no vision, ground truth in, one pose out.
"""

from __future__ import annotations

import numpy as np

from aisle.scenes.pharmacy import MED_NAMES


def select_pose(poses: np.ndarray, target_med: str) -> np.ndarray:
    """Pure selection: the target med's (7,) pos+quat block, scene-manifest
    order (TC-1)."""
    if target_med not in MED_NAMES:
        raise ValueError(f"unknown target_med {target_med!r}")
    flat = np.asarray(poses, dtype=np.float32).reshape(-1)
    i = MED_NAMES.index(target_med)
    return flat[i * 7 : (i + 1) * 7]


def main() -> None:
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.topics import make_sender

    node = Node()
    send = make_sender(node)
    target: str | None = None
    pending = False
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "target_request":
            request = json.loads(event["value"][0].as_py())
            if request.get("target_med") not in MED_NAMES:
                print(
                    f"target_request refused: unknown med {request.get('target_med')!r}",
                    file=sys.stderr,
                )
                continue
            target = request["target_med"]
            pending = True
        elif event["id"] == "reset_done":
            # episode boundary: a stale target must not keep emitting
            # target_pose (the pipeline would replan and pick the placed
            # box back out of the tray)
            target = None
            pending = False
        elif event["id"] == "poses" and target is not None and pending:
            # ONE target_pose per request: downstream plans exactly once
            # per episode, and a completed plan can never be re-triggered
            # by the still-flowing pose stream (simplify review)
            pending = False
            pose = select_pose(event["value"].to_numpy(zero_copy_only=False), target)
            # the med's identity rides with the pose so the grasp planner
            # can size the grip from meds.toml
            send("target_pose", pa.array(pose), {**metadata, "target_med": target})


if __name__ == "__main__":
    main()
