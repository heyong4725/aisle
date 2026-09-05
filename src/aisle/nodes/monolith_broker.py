"""monolith-broker — the trusted primitive broker of the monolithic arm
(SPEC 440, MON-3/MON-5/MON-6/MON-7).

One dora node replaces the typed graph's four agent-editable nodes
(segmented-pose, grasp-planner-topdown, ik-trajectory, task-state-machine).
It loads ONE agent-editable Python module named by the graph-attested
`AISLE_MONOLITH_MODULE`, hands it a frozen `Primitives` object (the same
pinned pose/grasp/trajectory implementations the typed nodes call), feeds
it the same semantic observations the typed nodes receive, and publishes
the actions it returns on the same edges — `joint_cmd`/`gripper_cmd` into
budget-guard, `episode_feedback` to rollout-client. The module never holds
a dora handle, a simulator handle, or the verifier.

Module contract (documented for the monolithic arm in docs/monolithic/):

    API_VERSION = "1.0"

    class Controller:
        def __init__(self, primitives, log): ...
        def on_event(self, event: dict) -> list[dict]: ...

`event` is {"name", "sim_time_ns", "payload", "goal_id"} for each name in
OBSERVATIONS; each returned action is one of {"joint_cmd": [..]},
{"gripper_cmd": x}, {"feedback": {..}}. The broker validates every action's
shape before it leaves; the guard clamps it exactly as it clamps the typed
executor's output (MON-5 same guard route).

Confinement: the module executes with the restricted builtins and guarded
importer of aisle.monolith.confinement; any denied route or a replaced
trusted callable produces an infrastructure-invalid record and stops the
node before the next command (MON-7). OS-level confinement is issue #353.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

from aisle.monolith.confinement import (
    ConfinementViolation,
    default_integrity,
    guarded,
    load_module,
)
from aisle.monolith.primitives import API_VERSION, Primitives

#: the semantic observation events a module receives, in the typed graph's
#: vocabulary (MON-4 same observation fields)
OBSERVATIONS = (
    "bridge_info",
    "seg_overhead",
    "depth_overhead",
    "rgb_overhead",
    "joint_state",
    "gripper_state",
    "violation",
    "reset_done",
    "episode_goal",
    "episode_result",
    "tick",
)
#: the actions a module may return (MON-4 same action names/authority)
ACTIONS = ("joint_cmd", "gripper_cmd", "feedback")

TICK_NS = 1_000_000_000  # the typed state machine's 1 Hz feedback cadence


def resolve_module_path(raw: str, root: Path | None = None) -> Path:
    """The module path from the graph env; a relative value is anchored at
    the repository root (the graph copy dora runs is under the run dir, so
    graph-relative would dangle)."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = root if root is not None else Path(__file__).resolve().parents[3]
        path = base / path
    return path


def module_record(path: Path, source: str, embodiment: str) -> dict:
    return {
        "module": str(path),
        "module_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "api_version": API_VERSION,
        "embodiment": embodiment,
    }


def validate_action(action: dict, n_dof: int) -> dict:
    """The broker's shape gate: one key from ACTIONS, numeric payloads of
    the declared arity. Anything else is a module error (ordinary runtime
    failure, MON-3), never a silently dropped command."""
    if not isinstance(action, dict) or len(action) != 1:
        raise TypeError(f"an action is a one-key dict from {ACTIONS}, got {action!r}")
    (name, value), *_ = action.items()
    if name not in ACTIONS:
        raise TypeError(f"unknown action {name!r}; allowed: {ACTIONS}")
    if name == "joint_cmd":
        arr = np.asarray(value, dtype=np.float32).reshape(-1)
        if arr.shape[0] != n_dof or not np.all(np.isfinite(arr)):
            raise ValueError(f"joint_cmd must be {n_dof} finite floats, got {arr.tolist()}")
        return {"joint_cmd": arr}
    if name == "gripper_cmd":
        g = float(value)
        if not np.isfinite(g):
            raise ValueError("gripper_cmd must be finite")
        return {"gripper_cmd": g}
    if not isinstance(value, dict):
        raise TypeError("feedback must be a JSON object")
    json.dumps(value)  # must serialize; raises otherwise
    return {"feedback": value}


class Broker:
    """Transport-free core: observation in, validated actions out, with the
    confinement checks around every module callback. `main()` is the dora
    shell around it."""

    def __init__(self, module_path: Path, embodiment: str, log=None) -> None:
        self.log = log or (lambda msg: print(msg, file=sys.stderr))
        self.embodiment = embodiment
        self.primitives = Primitives._load(embodiment)
        self.n_dof = int(self.primitives.home.shape[0])
        self.integrity = default_integrity()
        self.integrity.snapshot()
        source = module_path.read_text(encoding="utf-8")
        self.record = module_record(module_path, source, embodiment)
        with guarded():
            namespace = load_module(str(module_path), source)
            if namespace.get("API_VERSION") != API_VERSION:
                raise RuntimeError(
                    f"module declares API_VERSION={namespace.get('API_VERSION')!r}; "
                    f"broker speaks {API_VERSION}"
                )
            controller_cls = namespace.get("Controller")
            if controller_cls is None:
                raise RuntimeError("module defines no Controller class")
            self.controller = controller_cls(self.primitives, self.log)
        self.integrity.verify()
        self.goal_id = ""
        self.goal: dict | None = None
        self.ticks = 0
        self.last_tick_ns = -1

    def deliver(self, name: str, payload, sim_time_ns: int, goal_id: str = "") -> list[dict]:
        if name not in OBSERVATIONS:
            raise ValueError(f"not an observation: {name}")
        if name == "episode_goal":
            self.goal, self.goal_id, self.ticks = payload, goal_id, 0
            self.last_tick_ns = sim_time_ns
        elif name == "episode_result":
            self.goal = None
        event = {
            "name": name,
            "sim_time_ns": int(sim_time_ns),
            "payload": payload,
            "goal_id": goal_id or self.goal_id,
        }
        with guarded():
            raw = self.controller.on_event(event)
        self.integrity.verify()
        actions = [validate_action(a, self.n_dof) for a in (raw or [])]
        if name == "tick" and self.goal is not None and not any("feedback" in a for a in actions):
            # the harness contract holds the cadence fixed (MON-4): a module
            # that says nothing still reports the tick
            actions.append({"feedback": {"t": self.ticks, "phase": "unknown"}})
        return actions

    def ticks_due(self, sim_time_ns: int) -> list[int]:
        """1 Hz ticks derived from the turn clock, as the typed state
        machine derives them (CON-5)."""
        if self.goal is None:
            return []
        if self.last_tick_ns < 0:
            self.last_tick_ns = sim_time_ns
        due = []
        while sim_time_ns - self.last_tick_ns >= TICK_NS:
            self.last_tick_ns += TICK_NS
            self.ticks += 1
            due.append(self.ticks)
        return due


def _frame(event, metadata: dict, dtype, channels: int | None):
    h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
    if h <= 0 or w <= 0:
        return None
    frame = np.asarray(event["value"].to_numpy(zero_copy_only=False)).astype(dtype)
    return frame.reshape(h, w, channels) if channels else frame.reshape(h, w)


def _invalid_record(reason: str) -> None:
    """MON-7: the infrastructure-invalid record lands beside the run's
    episode results before the node stops."""
    from aisle.harness.monolithic import TypedSurfaceError, classify_bypass_attempt

    try:
        category = classify_bypass_attempt(reason)
    except TypedSurfaceError:
        category = "unclassified"
    results = os.environ.get("AISLE_RESULTS")
    payload = {
        "infrastructure_invalid": True,
        "node": "monolith-broker",
        "reason": reason,
        "category": category,
    }
    if results:
        path = Path(results).parent / "monolith_invalid.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload), file=sys.stderr)


def main() -> None:  # pragma: no cover — dora runtime
    import pyarrow as pa

    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    raw = os.environ.get("AISLE_MONOLITH_MODULE", "").strip()
    if not raw:
        raise RuntimeError("AISLE_MONOLITH_MODULE (graph env) names the monolithic module")
    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    try:
        broker = Broker(resolve_module_path(raw), embodiment)
    except ConfinementViolation as exc:
        _invalid_record(str(exc))
        raise SystemExit(3) from exc
    results = os.environ.get("AISLE_RESULTS")
    if results:
        (Path(results).parent / "monolith.json").write_text(
            json.dumps(broker.record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)

    def publish(actions: list[dict], metadata: dict) -> None:
        for action in actions:
            if "joint_cmd" in action:
                send("joint_cmd", pa.array(action["joint_cmd"]), metadata)
            elif "gripper_cmd" in action:
                send(
                    "gripper_cmd",
                    pa.array(np.array([action["gripper_cmd"]], dtype=np.float32)),
                    metadata,
                )
            else:
                send(
                    "episode_feedback",
                    pa.array([json.dumps(action["feedback"])]),
                    {**metadata, "goal_id": broker.goal_id},
                )

    for event in node:
        if event["type"] != "INPUT":
            continue
        topic, metadata = event["id"], (event.get("metadata") or {})
        if not env_accepts(metadata, env_pin):
            continue
        stamp_ns = int(metadata.get("sim_time_ns", 0))
        try:
            if topic == "turn":
                for _ in broker.ticks_due(stamp_ns):
                    publish(broker.deliver("tick", broker.ticks, stamp_ns), metadata)
                continue
            if topic in ("bridge_info", "violation", "episode_goal", "episode_result"):
                payload = json.loads(event["value"][0].as_py())
                actions = broker.deliver(topic, payload, stamp_ns, metadata.get("goal_id", ""))
            elif topic == "reset_done":
                actions = broker.deliver(topic, None, stamp_ns)
            elif topic == "seg_overhead":
                frame = _frame(event, metadata, np.int32, None)
                actions = [] if frame is None else broker.deliver(topic, frame, stamp_ns)
            elif topic == "depth_overhead":
                frame = _frame(event, metadata, np.float32, None)
                actions = [] if frame is None else broker.deliver(topic, frame, stamp_ns)
            elif topic == "rgb_overhead":
                frame = _frame(event, metadata, np.uint8, 3)
                actions = [] if frame is None else broker.deliver(topic, frame, stamp_ns)
            elif topic in ("joint_state", "gripper_state"):
                vec = np.asarray(
                    event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
                ).reshape(-1)
                actions = broker.deliver(topic, vec, stamp_ns)
            else:
                continue
        except ConfinementViolation as exc:
            _invalid_record(str(exc))
            node.stop_after_turn()
            continue
        clean = {k: v for k, v in metadata.items() if k not in ("enc", "h", "w")}
        publish(actions, clean)


if __name__ == "__main__":  # pragma: no cover
    main()
