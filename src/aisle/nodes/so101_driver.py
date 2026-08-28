"""so101-driver node (Phase 6 prep, ADR-phase6-prep).

The hardware side of SPEC 010's sim->real claim: the SAME topic surface
the graphs already speak — `joint_state`/`gripper_state` out at the
TC-4 cadence, `joint_cmd`/`gripper_cmd` in, TC-2 stamps — served from
an SO-101 bus instead of the simulator. Hardware is free-run by design
(ADR-30 lockstep is a sim instrument); `sim_time_ns` carries the
driver's monotonic hardware clock, honestly labeled by `bridge_info`.

The bus is injected: `AISLE_SO101_PORT=<serial>` drives a real lerobot
SO101Follower (imported ONLY then — CI and the unit suite never touch
lerobot); `AISLE_SO101_PORT=loopback` runs LoopbackBus, a pure-python
double with first-order lag over the frozen embodiment joint order, so
every contract behavior is testable without hardware.

Reset REFUSES (TC-6/ADR-34): hardware has no teleport — the reply
rides `reset_refused` with the reason, and episode boundaries use the
behavioral-reset path Phase 6 inherits from A6. Per-tick command
deltas are clamped to MAX_STEP_RAD as defense in depth UNDER the
budget guard, which remains the frozen authority.
"""

from __future__ import annotations

import numpy as np

from aisle.embodiment import SO101_ARM_JOINTS, SO101_JOINTS

DT_NS = 10_000_000  # TC-4 joint_state cadence (100 Hz)
MAX_STEP_RAD = 0.05  # per-tick command delta clamp (driver-level belt)
N_ARM = len(SO101_ARM_JOINTS)


def clamp_step(current: np.ndarray, target: np.ndarray, max_step: float = MAX_STEP_RAD):
    """The driver-level per-tick delta clamp (defense in depth UNDER the
    guard): the commanded move this tick never exceeds max_step per
    joint, whatever arrives on the wire."""
    delta = np.clip(target - current, -max_step, max_step)
    return (current + delta).astype(np.float32)


class LoopbackBus:
    """A pure-python SO-101 double: first-order lag toward the last
    command, frozen joint order, no I/O. Exists so the driver's contract
    behavior is testable and CI-safe today (ADR-phase6-prep)."""

    def __init__(self, home: np.ndarray | None = None) -> None:
        self.q = (
            np.zeros(len(SO101_JOINTS), dtype=np.float32)
            if home is None
            else np.asarray(home, dtype=np.float32).copy()
        )
        self.target = self.q.copy()
        self.connected = True

    def write_positions(self, q_cmd: np.ndarray) -> None:
        self.target = np.asarray(q_cmd, dtype=np.float32).copy()

    def read_positions(self) -> np.ndarray:
        self.q = clamp_step(self.q, self.target, MAX_STEP_RAD)
        return self.q.copy()

    def disconnect(self) -> None:
        self.connected = False


class LerobotBus:  # pragma: no cover — hardware-only by construction
    """The real bus: lerobot's SO101Follower behind the same three
    calls. Imported lazily so nothing outside a real port ever needs
    lerobot installed."""

    def __init__(self, port: str, calibration_dir: str | None) -> None:
        from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig

        config = SO101FollowerConfig(
            port=port,
            disable_torque_on_disconnect=True,
            max_relative_target=float(np.degrees(MAX_STEP_RAD)),
            calibration_dir=calibration_dir,
            id="aisle-so101",
        )
        self.robot = SO101Follower(config)
        self.robot.connect()
        self.connected = True

    def write_positions(self, q_cmd: np.ndarray) -> None:
        action = {f"{name}.pos": float(np.degrees(q_cmd[i])) for i, name in enumerate(SO101_JOINTS)}
        self.robot.send_action(action)

    def read_positions(self) -> np.ndarray:
        obs = self.robot.get_observation()
        return np.array(
            [np.radians(float(obs[f"{name}.pos"])) for name in SO101_JOINTS],
            dtype=np.float32,
        )

    def disconnect(self) -> None:
        self.robot.disconnect()
        self.connected = False


def make_bus(port: str, calibration_dir: str | None = None):
    """The injection point: 'loopback' -> the pure double; anything else
    -> the lerobot bus (the only lerobot import site). An empty port is
    a configuration error, never a silent default to hardware."""
    if not port:
        raise ValueError("AISLE_SO101_PORT is required: 'loopback' or a serial device path")
    if port == "loopback":
        return LoopbackBus()
    return LerobotBus(port, calibration_dir)


def main() -> None:  # pragma: no cover — graph runtime
    import json
    import os
    import sys
    import time

    import pyarrow as pa
    from dora import Node

    from aisle.topics import stamp

    port = os.environ.get("AISLE_SO101_PORT", "")
    bus = make_bus(port, os.environ.get("AISLE_SO101_CALIBRATION_DIR") or None)
    print(f"so101-driver: bus={'loopback' if port == 'loopback' else port}", file=sys.stderr)

    node = Node()
    seq: dict[str, int] = {}
    t0 = time.monotonic_ns()

    def send(topic: str, value, metadata: dict | None = None) -> None:
        seq[topic] = seq.get(topic, 0) + 1
        node.send_output(topic, value, stamp({**(metadata or {})}, seq[topic]))

    target: np.ndarray | None = None
    grip_target: float | None = None
    sent_info = False
    try:
        for event in node:
            if event["type"] != "INPUT":
                continue
            eid = event["id"]
            metadata = event.get("metadata") or {}
            if eid == "joint_cmd":
                cmd = np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.float32)
                target = cmd[:N_ARM]
            elif eid == "gripper_cmd":
                grip_target = float(
                    np.asarray(event["value"].to_numpy(zero_copy_only=False)).reshape(-1)[0]
                )
            elif eid == "reset":
                # TC-6/ADR-34: hardware has no teleport — refuse with the
                # reason; the behavioral-reset path owns episode boundaries
                send(
                    "reset_refused",
                    pa.array(np.array([0], dtype=np.uint32)),
                    {
                        "request_id": metadata.get("request_id", ""),
                        "reason": "hardware has no teleport (Phase 6): behavioral reset only",
                    },
                )
            elif eid == "tick":
                now_ns = time.monotonic_ns() - t0
                q = bus.read_positions()
                if target is not None or grip_target is not None:
                    q_cmd = q.copy()
                    if target is not None:
                        q_cmd[:N_ARM] = clamp_step(q[:N_ARM], target)
                    if grip_target is not None:
                        q_cmd[N_ARM:] = grip_target
                    bus.write_positions(q_cmd)
                meta = {"sim_time_ns": int(now_ns), "env_id": 0}
                send("joint_state", pa.array(q), meta)
                send("gripper_state", pa.array(q[N_ARM:][:1]), meta)
                if not sent_info:
                    send(
                        "bridge_info",
                        pa.array(
                            [
                                json.dumps(
                                    {
                                        "backend": "so101-hardware",
                                        "clock": "hardware-monotonic",
                                        "perception": "hardware",
                                        "n_envs": 1,
                                    }
                                )
                            ]
                        ),
                        meta,
                    )
                    sent_info = True
    finally:
        bus.disconnect()


if __name__ == "__main__":
    main()
