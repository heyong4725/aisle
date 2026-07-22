"""dora-genesis bridge node (SPEC 030, implementing SPEC 010 over SPEC 020).

Exactly one bridge owns the Genesis scene per dataflow (BRG-1). The node is
driven by dora/timer/millis/10 ticks; each tick advances sim by cfg.dt,
services coalesced commands in arrival order (BRG-3), and publishes topics
at their contract rates (TC table) — rendering only when a camera topic is
due (BRG-2). Pure control-plane logic (scheduler, coalescer, config,
bridge_info) lives at module level, sim-free and unit-tested; dora, arrow,
and genesis are imported only inside main() (CON-12).

Sim exceptions propagate: there is deliberately no try/except around
scene.step() or state injection — a physics error must crash the node
loudly as a dora ERROR event (BRG-7).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]

# SPEC 010 §2: producer rates are contracts, not hints (TC-4)
TOPIC_RATES = {
    "rgb_overhead": 30,
    "rgb_wrist": 30,
    "depth_overhead": 15,
    "joint_state": 100,
    "gripper_state": 100,
    "oracle_state": 30,
    # non-privileged ground-truth poses for tier-T0 perception (SPEC 010,
    # issue #2 resolution); same payload as oracle_state, separate topic so
    # VAL-6 keeps oracle_state verifier-only. 15 Hz: a second 30 Hz stream
    # pushed the render wall-rate below the TC-4 band (T08 A1)
    "poses": 15,
}
RENDER_TOPICS = ("rgb_overhead", "rgb_wrist", "depth_overhead")
# ticks after a reset during which the bridge HOLDS the arm at home and
# drops incoming joint commands. A collision/timeout ends an episode
# mid-plan; the executor keeps streaming that plan's joint_cmds for the
# few ticks until it receives reset_done and clears, and those stale
# commands would drive the just-homed arm back off home — the next
# episode then starts from a bad pose and sweeps the shelf (M0 run
# t10-clearcheck, ep9 cascade). 20 ticks (0.2 s) covers the executor's
# reset_done round-trip and is far shorter than the goal->grasp latency,
# so no real command for the NEW episode is dropped.
RESET_SETTLE_TICKS = 20


@dataclass(frozen=True)
class BridgeConfig:
    seed: int
    embodiment: str
    n_envs: int


def parse_bridge_config(env: dict) -> BridgeConfig:
    """BRG-1: node configuration from environment variables."""
    return BridgeConfig(
        seed=int(env.get("AISLE_SEED", "0")),
        embodiment=env.get("AISLE_EMBODIMENT", "franka"),
        n_envs=int(env.get("AISLE_N_ENVS", "1")),
    )


class ResetQuarantine:
    """BRG-4: after a reset the executor keeps streaming the ended episode's
    plan for a few ticks until it receives reset_done and clears. Those
    stale joint_cmds would drive the just-teleported-home arm back off home,
    so the bridge holds the arm at home and DROPS commands while quarantined
    — `arm()` on reset, `hold()` once per tick returns True while active and
    consumes one tick (M0 run t10-clearcheck ep9 cascade)."""

    def __init__(self, ticks: int):
        self.ticks = int(ticks)
        self._remaining = 0

    def arm(self) -> None:
        self._remaining = self.ticks

    def hold(self) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False


class RateScheduler:
    """Integer-exact per-topic rate divider: topic fires when the count of
    contract periods elapsed exceeds the count already fired. No float
    accumulation drift (CON-5)."""

    def __init__(self, rates: dict[str, int], dt: float):
        self.rates = dict(rates)
        self.dt = dt
        self.tick = 0
        self.fired = dict.fromkeys(rates, 0)

    def due(self) -> list[str]:
        self.tick += 1
        fired = []
        for topic, rate in self.rates.items():
            target = int(self.tick * self.dt * rate + 1e-9)
            if target > self.fired[topic]:
                fired.append(topic)
                self.fired[topic] = target
        return fired


class CommandQueue:
    """BRG-1/BRG-3/BRG-5: keep only the latest command per (kind, env)
    between ticks, counting superseded ones — but preserve ARRIVAL ORDER
    across kinds when applying (joint_cmd spans all dofs incl. fingers, so
    whichever command arrived last must win). Missing env_id is an error in
    multi-env mode and defaults to 0 in single-env mode (TC-2); env_id must
    be an int within [0, n_envs)."""

    def __init__(self, n_envs: int):
        self.n_envs = n_envs
        self._arrival = 0
        self._pending: dict[tuple[str, int], tuple[object, int, int]] = {}

    def push(self, kind: str, env_id: int | None, payload) -> None:
        if env_id is None:
            if self.n_envs > 1:
                raise ValueError(f"{kind} missing env_id in multi-env mode (BRG-5)")
            env_id = 0
        # strictly integral: bool/float coercion would silently misroute
        # (0.7 -> env 0, True -> env 1)
        if isinstance(env_id, bool) or not isinstance(env_id, int):
            raise ValueError(f"{kind} env_id must be an int, got {env_id!r} (BRG-5)")
        if not 0 <= env_id < self.n_envs:
            raise ValueError(f"{kind} env_id {env_id} outside [0, {self.n_envs}) (BRG-5)")
        self._arrival += 1
        key = (kind, env_id)
        dropped = self._pending[key][1] + 1 if key in self._pending else 0
        self._pending[key] = (payload, dropped, self._arrival)

    def drain(self) -> list[tuple[str, int, object, int]]:
        """(kind, env_id, payload, dropped) in arrival order of each
        surviving command."""
        items = sorted(self._pending.items(), key=lambda kv: kv[1][2])
        self._pending = {}
        return [(kind, env, payload, dropped) for (kind, env), (payload, dropped, _) in items]


def make_bridge_info(
    embodiment: str, n_dof: int, n_envs: int, genesis_version: str, env_hash: str
) -> str:
    """BRG-6: the startup contract announcement, as a JSON string."""
    return json.dumps(
        {
            "contract": "v0",
            "embodiment": embodiment,
            "n_dof": n_dof,
            "n_envs": n_envs,
            "genesis_version": genesis_version,
            "platform": f"{platform.system().lower()}-{platform.machine()}",
            "env_hash": env_hash,
        }
    )


def compute_env_hash(root: Path) -> str:
    """BRG-6/CON-7: the frozen-set hash, via the canonical tool."""
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "env_hash.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)["env_hash"]


def _metadata(sim_time_ns: int, env_id: int, seq: int, **extra) -> dict:
    """TC-2: mandatory metadata on every output message."""
    return {"sim_time_ns": sim_time_ns, "env_id": env_id, "seq": seq, **extra}


def main(clock: Callable[[], float] = time.perf_counter) -> None:
    """The clock is injected (CON-5): reset timing must never reach for a
    wall clock ad hoc."""
    import genesis
    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import (
        build_scene,
        load_physics,
        oracle_state,
        resolve_layout,
        sample_placements,
        to_numpy,
    )

    cfg = parse_bridge_config(os.environ)
    root = Path(os.environ.get("AISLE_ROOT", _REPO_ROOT))
    physics = load_physics()
    profile = physics["embodiment"][cfg.embodiment]
    dt = physics["sim"]["dt"]

    handle = build_scene(seed=cfg.seed, embodiment=cfg.embodiment, n_envs=cfg.n_envs, headless=True)
    robot = handle.robot
    n_dof = robot.n_dofs

    node = Node()
    node.send_output(
        "bridge_info",
        pa.array(
            [
                make_bridge_info(
                    embodiment=cfg.embodiment,
                    n_dof=n_dof,
                    n_envs=cfg.n_envs,
                    genesis_version=genesis.__version__,
                    env_hash=compute_env_hash(root),
                )
            ]
        ),
        metadata=_metadata(0, 0, 0),
    )

    scheduler = RateScheduler(TOPIC_RATES, dt)
    commands = CommandQueue(cfg.n_envs)
    seq: dict[tuple[str, int], int] = {}
    dropped_counts: dict[str, dict[int, int]] = {"joint": {}, "gripper": {}}
    sim_time_ns = 0
    quarantine = ResetQuarantine(RESET_SETTLE_TICKS)  # holds arm at home post-reset
    home_hold = (
        np.asarray(profile["home_qpos"], dtype=np.float32) if "home_qpos" in profile else None
    )
    # one name per DOF in payload order (TC-5): multi-dof joints repeat,
    # zero-dof (fixed) joints vanish; a mismatch is a loud startup failure
    joint_names = []
    for joint in robot.joints:
        joint_names += [joint.name] * int(getattr(joint, "n_dofs", 1))
    assert len(joint_names) == n_dof, (len(joint_names), n_dof)
    gripper_open = profile.get("gripper_open_m", 0.04)
    gripper_close = profile.get("gripper_close_m", 0.0)
    gripper_dofs = int(profile.get("gripper_dofs", 2))
    finger_idx = list(range(n_dof - gripper_dofs, n_dof))

    def send(topic: str, env_id: int, array: np.ndarray, **extra) -> None:
        key = (topic, env_id)
        seq[key] = seq.get(key, 0) + 1
        node.send_output(
            topic,
            pa.array(np.ravel(array)),
            metadata=_metadata(sim_time_ns, env_id, seq[key], **extra),
        )

    def env_slice(tensor, env_id: int) -> np.ndarray:
        data = to_numpy(tensor)
        return data[env_id] if cfg.n_envs > 1 else data.reshape(-1)

    def render_due(due: list[str]) -> dict[str, np.ndarray]:
        """BRG-2: one overhead pass serves both rgb and depth when both are
        due; nothing renders unless a camera topic is due this tick."""
        frames: dict[str, np.ndarray] = {}
        need_rgb = "rgb_overhead" in due
        need_depth = "depth_overhead" in due
        if need_rgb or need_depth:
            out = handle.cams["overhead"].render(rgb=True, depth=need_depth)
            frames["rgb_overhead"] = np.asarray(out[0], dtype=np.uint8)
            if need_depth:
                frames["depth_overhead"] = np.asarray(out[1], dtype=np.float32)
        if "rgb_wrist" in due:
            frames["rgb_wrist"] = np.asarray(handle.cams["wrist"].render()[0], dtype=np.uint8)
        return frames

    def publish(topic: str, frames: dict[str, np.ndarray] | None = None) -> None:
        oracle_cache = None
        frames = frames if frames is not None else render_due([topic])
        qpos = robot.get_qpos() if topic in ("joint_state", "gripper_state") else None
        # camera topics: genesis batched scenes render ONE view; publishing
        # it per env would mislabel pixels (ADR-7) — env 0 only
        n_targets = 1 if topic in RENDER_TOPICS else cfg.n_envs
        for env_id in range(n_targets):
            if topic == "joint_state":
                send(
                    topic,
                    env_id,
                    env_slice(qpos, env_id),
                    names=joint_names,
                    dropped=dropped_counts["joint"].pop(env_id, 0),
                )
            elif topic == "gripper_state":
                finger = env_slice(qpos, env_id)[-1]
                width = np.float32((gripper_open - finger) / (gripper_open - gripper_close or 1.0))
                send(
                    topic,
                    env_id,
                    np.clip(width, 0.0, 1.0),
                    dropped=dropped_counts["gripper"].pop(env_id, 0),
                )
            elif topic in ("oracle_state", "poses"):
                if oracle_cache is None:
                    oracle_cache = oracle_state(handle)
                send(topic, env_id, oracle_cache[env_id] if cfg.n_envs > 1 else oracle_cache)
            elif topic in ("rgb_overhead", "rgb_wrist"):
                rgb = frames[topic]
                send(topic, env_id, rgb, h=rgb.shape[0], w=rgb.shape[1], enc="rgb8")
            elif topic == "depth_overhead":
                depth = frames[topic]
                send(topic, env_id, depth, h=depth.shape[0], w=depth.shape[1], enc="depth32f")

    def apply_commands() -> None:
        # BRG-1: apply in arrival order across kinds — the last-arrived
        # command owns any overlapping dofs
        for kind, env_id, payload, dropped in commands.drain():
            if kind == "joint":
                target = np.asarray(payload, dtype=np.float32)
                if cfg.n_envs > 1:
                    robot.control_dofs_position(target[None, :], envs_idx=[env_id])
                else:
                    robot.control_dofs_position(target)
            else:
                width = float(np.asarray(payload).reshape(-1)[0])
                finger = gripper_open - width * (gripper_open - gripper_close)
                # ONLY the embodiment's gripper dofs (so101 has one, franka
                # two): an all-dof write would cancel the arm trajectory
                finger_target = np.full(len(finger_idx), finger, dtype=np.float32)
                if cfg.n_envs > 1:
                    robot.control_dofs_position(
                        finger_target[None, :], dofs_idx_local=finger_idx, envs_idx=[env_id]
                    )
                else:
                    robot.control_dofs_position(finger_target, dofs_idx_local=finger_idx)
            dropped_counts[kind][env_id] = dropped_counts[kind].get(env_id, 0) + dropped

    def teleport_reset(seed: int) -> None:
        """BRG-4: state injection from a fresh placement sample — no process
        restart, no scene rebuild."""
        layout = resolve_layout(physics, cfg.embodiment)
        for placement in sample_placements(seed, list(handle.boxes), layout):
            entity = handle.boxes[placement.name]
            pos = np.array([placement.x, placement.y, placement.z], dtype=np.float32)
            quat = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)  # genesis wxyz
            if cfg.n_envs > 1:
                entity.set_pos(np.tile(pos, (cfg.n_envs, 1)))
                entity.set_quat(np.tile(quat, (cfg.n_envs, 1)))
            else:
                entity.set_pos(pos)
                entity.set_quat(quat)
            entity.zero_all_dofs_velocity()
        if "home_qpos" in profile:
            home = np.asarray(profile["home_qpos"], dtype=np.float32)
            batched_home = home if cfg.n_envs == 1 else np.tile(home, (cfg.n_envs, 1))
            robot.set_qpos(batched_home)
            # re-latch the PD controller: a stale pre-reset target would
            # drive the arm away from home on the first post-reset tick
            robot.control_dofs_position(batched_home)
        robot.zero_all_dofs_velocity()
        # pre-reset commands must not leak into the new episode (CON-5)
        commands.drain()
        for counts in dropped_counts.values():
            counts.clear()
        # hold the arm at home for the next few ticks: the executor keeps
        # streaming the ended episode's plan until it sees reset_done, and
        # those in-flight joint_cmds would otherwise drive the arm off home
        if home_hold is not None:
            quarantine.arm()

    for event in node:
        if event["type"] != "INPUT":
            continue
        input_id = event["id"]
        metadata = event.get("metadata") or {}
        if input_id == "tick":
            if home_hold is not None and quarantine.hold():
                # post-reset settle: hold the arm at home and DROP any stale
                # joint_cmds still in flight from the ended episode's plan,
                # so they cannot drive the just-homed arm off home
                commands.drain()
                batched = home_hold if cfg.n_envs == 1 else np.tile(home_hold, (cfg.n_envs, 1))
                robot.control_dofs_position(batched)
            else:
                apply_commands()
            handle.scene.step()  # BRG-7: exceptions crash the node loudly
            sim_time_ns += int(dt * 1e9)
            due = scheduler.due()
            frames = render_due(due)
            for topic in due:
                publish(topic, frames)
        elif input_id == "joint_cmd":
            payload = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if payload.shape[0] != n_dof:
                raise ValueError(
                    f"joint_cmd must be Float32[{n_dof}], got length {payload.shape[0]} (TC-5)"
                )
            commands.push("joint", metadata.get("env_id"), payload)
        elif input_id == "gripper_cmd":
            payload = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if payload.shape[0] != 1 or not 0.0 <= float(payload[0]) <= 1.0:
                raise ValueError(
                    f"gripper_cmd must be Float32[1] in [0, 1], got {payload!r} (TC table)"
                )
            commands.push("gripper", metadata.get("env_id"), payload)
        elif input_id == "reset":
            started = clock()
            payload = np.asarray(event["value"].to_numpy(zero_copy_only=False)).reshape(-1)
            if payload.shape[0] != 2:
                raise ValueError(f"reset payload must be UInt32[2], got {payload.shape} (TC-6)")
            reset_seed, mode = int(payload[0]), int(payload[1])
            if mode not in (0, 1):
                raise ValueError(f"reset mode must be 0 or 1, got {mode} (TC-6)")
            if not metadata.get("request_id"):
                raise ValueError("reset request missing request_id metadata (TC-6)")
            # TC-6: no observation may interleave reset -> reset_done; the
            # loop is single-threaded, so replying before returning to the
            # event loop guarantees ordering
            if mode == 1:
                raise NotImplementedError("behavioral reset lands with SPEC 040 (T06)")
            teleport_reset(reset_seed)
            node.send_output(
                "reset_done",
                pa.array(np.array([1], dtype=np.uint32)),
                metadata=_metadata(
                    sim_time_ns,
                    0,
                    seq.update({("reset_done", 0): seq.get(("reset_done", 0), 0) + 1})
                    or seq[("reset_done", 0)],
                    request_id=metadata.get("request_id", ""),
                    seed=reset_seed,
                    mode=mode,
                    t_reset_ms=int((clock() - started) * 1000),
                ),
            )
            # the injected state IS the post-reset observation: snapshot it
            # before any physics step so the first oracle_state after reset
            # is a pure function of the seed (TC-A2, CON-5); reset_done was
            # already sent, so nothing interleaves the service pair (TC-6)
            publish("oracle_state")
            publish("poses")


if __name__ == "__main__":
    main()
