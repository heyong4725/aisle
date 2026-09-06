"""semantic-gateway — the SPEC 480 semantic authorization boundary inside a
live dataflow (issue #352, SEM-2/4/5/8).

Sits between the executor (ik-trajectory) and budget-guard. Every joint
or gripper command the policy emits is a *proposal*; the gateway derives
its SEM-5 stage from the motion itself — `pre_grasp` at the open-to-closed
gripper transition, `carry` while the gripper is closed, `delivery` while
closed and the tool centre point is over the tray — asks the trusted
`SemanticAuthorizer` for a single-use permit, verifies it through the
`PermitGateway`, and forwards the command only with a valid permit. A
refusal holds the arm: the last forwarded joint command is re-sent (no new
motion), the gripper command is dropped, and a `shield_event` records the
refusal with its reason. Commands outside any stage (gripper open, no
carried object) pass without a permit, exactly as the corpus replay
treats pre-grasp approach.

Identity adapters (`AISLE_SHIELD_ARM`):
- `oracle_sim_shield`: the ground-truth `poses` topic of a rung-L0 graph —
  the ceiling arm; which box is at the tool centre point is read from the
  simulator. This is NOT a deployable shield (SEM-14).
- `sensor_shield`: reserved for the rendered-perception adapter; refuses
  (no permit) until it exists, so a graph that wires it fails closed.
- `no_shield`: every proposal is forwarded; the authorizer still logs, so
  the same evidence exists for the control arm.

The kinematic guard downstream is untouched in every arm (SEM-8): a guard
clamp or containment event is a separate record the analysis excludes
from the semantic contrast by rule, never credited to the shield.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

from aisle.harness.semantic_shield import (
    ARMS,
    PermitGateway,
    SemanticAuthorizer,
    content_hash,
)

CARRY_RADIUS_M = 0.06  # a box centre within this of the TCP is the carried object
CANDIDATE_RADIUS_M = 0.10  # at closure, the nearest box within this is the grasp candidate
IDENTITY_SOURCE = "simulation_oracle"


def stage_of(gripper_closed: bool, closing_edge: bool, tcp_over_tray: bool) -> str | None:
    """SEM-5 stage of one proposal, or None when no authorization applies."""
    if closing_edge:
        return "pre_grasp"
    if not gripper_closed:
        return None
    return "delivery" if tcp_over_tray else "carry"


def nearest_box(poses: np.ndarray, tcp: np.ndarray, med_names: list[str]) -> tuple[str, float]:
    flat = np.asarray(poses, dtype=np.float64).reshape(-1)
    best, best_d = "", float("inf")
    for i, name in enumerate(med_names):
        d = float(np.linalg.norm(flat[i * 7 : i * 7 + 3] - tcp))
        if d < best_d:
            best, best_d = name, d
    return best, best_d


def over_tray(tcp: np.ndarray, tray: dict) -> bool:
    pos, size = tray["pos"], tray["size"]
    return abs(float(tcp[0]) - pos[0]) <= size[0] / 2 and abs(float(tcp[1]) - pos[1]) <= size[1] / 2


class OracleIdentity:
    """The ceiling adapter: identity of the box at the tool centre point
    from the simulator's own poses, expressed as a SEM-3 assertion."""

    def __init__(self, med_names: list[str]):
        self.med_names = med_names
        self.poses: np.ndarray | None = None
        self.capture_s: float = -1.0
        self.n = 0
        self.source_hash = hashlib.sha256(IDENTITY_SOURCE.encode()).hexdigest()

    def on_poses(self, poses: np.ndarray, sim_time_s: float) -> None:
        self.poses, self.capture_s = np.asarray(poses, dtype=np.float32), sim_time_s

    def assertion(self, tcp: np.ndarray, radius: float, now_s: float) -> tuple[dict, str | None]:
        """One assertion for the object at the TCP (or a refusal when no box
        is within `radius`); returns (assertion, track_id or None)."""
        self.n += 1
        refused, classes, track = True, {}, None
        if self.poses is not None:
            name, dist = nearest_box(self.poses, tcp, self.med_names)
            if dist <= radius:
                refused, classes, track = False, {name: 1.0}, name
        return {
            "assertion_id": f"oracle-{self.n:06d}",
            "source_hash": self.source_hash,
            "observation_id": f"poses@{self.capture_s:.3f}",
            "track_id": track or "none",
            "carrier": "gripper" if not refused else None,
            "classes": classes,
            "refused": refused,
            "capture_s": self.capture_s,
            "receipt_s": now_s,
            "in_envelope": self.poses is not None,
            "evidence_kind": "simulation_oracle",
        }, track


class Gateway:
    """Transport-free core, unit-testable without dora."""

    def __init__(self, arm: str, key: bytes, med_names: list[str], tray: dict, grasp_cmd: float):
        if arm not in ARMS:
            raise ValueError(f"unknown shield arm {arm!r}; expected one of {ARMS}")
        self.arm = arm
        self.tray = tray
        self.grasp_cmd = grasp_cmd
        self.identity = OracleIdentity(med_names)
        self.authorizer = SemanticAuthorizer(key, {self.identity.source_hash})
        self.permits = PermitGateway(key, enforce=arm != "no_shield")
        self.assignment: dict | None = None
        self.gripper_closed = False
        self.last_forwarded: np.ndarray | None = None
        self.proposals = 0
        self.events: list[dict] = []

    # -- episode -----------------------------------------------------------
    def on_goal(self, goal: dict, goal_id: str, now_s: float, vocabulary: list[str]) -> None:
        self.assignment = {
            "assignment_id": content_hash({"goal": goal, "goal_id": goal_id}),
            "episode_id": goal_id,
            "goal_revision": int(goal.get("goal_revision", 0)),
            "target_identity": goal["target_med"],
            "vocabulary": list(vocabulary),
            "valid_from_s": now_s,
            "valid_to_s": now_s + float(goal.get("timeout_s", 3600.0)),
        }
        self.authorizer.on_assignment(self.assignment)
        self.gripper_closed = False

    def on_reset(self) -> None:
        self.assignment = None
        self.gripper_closed = False
        self.last_forwarded = None
        self.authorizer.restart()

    # -- proposals ---------------------------------------------------------
    def _proposal(self, kind: str, value, stage: str, track: str | None) -> dict:
        return {
            "proposal_id": f"p-{self.proposals:06d}",
            "proposal_hash": content_hash({"kind": kind, "value": np.asarray(value).tolist()}),
            "stage": stage,
            "track_id": track or "none",
            "carrier": "gripper",
            "episode_id": self.assignment["episode_id"],
            "goal_revision": self.assignment["goal_revision"],
        }

    def propose(self, kind: str, value: np.ndarray, tcp: np.ndarray, now_s: float) -> dict:
        """Decide one command. Returns {"forward": bool, "value": array|None,
        "stage": str|None, "reason": str|None, "halt": bool}."""
        closing_edge = False
        if kind == "gripper_cmd":
            closed = float(value[0]) >= self.grasp_cmd - 1e-6
            closing_edge = closed and not self.gripper_closed
        stage = stage_of(self.gripper_closed, closing_edge, over_tray(tcp, self.tray))
        if stage is None or self.assignment is None:
            if kind == "gripper_cmd":
                self.gripper_closed = float(value[0]) >= self.grasp_cmd - 1e-6
            elif kind == "joint_cmd":
                self.last_forwarded = np.asarray(value, dtype=np.float32)
            return {"forward": True, "value": value, "stage": stage, "reason": None, "halt": False}
        radius = CANDIDATE_RADIUS_M if stage == "pre_grasp" else CARRY_RADIUS_M
        assertion, track = self.identity.assertion(tcp, radius, now_s)
        if self.arm == "sensor_shield":
            assertion = {**assertion, "refused": True, "classes": {}}  # adapter absent
        # the authorizer keeps every assertion; at the joint_state cadence
        # that list would grow without bound and its scan would stall the
        # turn (watchdog in the first live run) — keep the evidence window
        horizon = now_s - 2.0 * float(self.authorizer.config["max_age_s"])
        state = self.authorizer.state
        state.assertions = [a for a in state.assertions if float(a["receipt_s"]) >= horizon]
        rejected = self.authorizer.on_assertion(assertion)
        self.proposals += 1
        proposal = self._proposal(kind, value, stage, track)
        if stage == "delivery":
            # SEM-5: delivery entry needs a carry permit renewed within
            # renewal_s; over the tray every command is a delivery
            # proposal, so the carried identity is re-attested first
            self.authorizer.request({**proposal, "stage": "carry"}, now_s)
        decision = self.authorizer.request(proposal, now_s)
        check = self.permits.check(decision.get("permit"), proposal, self.assignment, now_s)
        forward = check["forwarded"]
        event = {
            "t_s": now_s,
            "kind": kind,
            "stage": stage,
            "track": track,
            "assertion_rejected": rejected,
            "outcome": "permit" if decision.get("permit") else "refuse",
            "reason": decision.get("reason"),
            "forwarded": forward,
            "halt": bool(decision.get("halt_requested", False)) and not forward,
        }
        self.events.append(event)
        if forward:
            if kind == "gripper_cmd":
                self.gripper_closed = float(value[0]) >= self.grasp_cmd - 1e-6
            else:
                self.last_forwarded = np.asarray(value, dtype=np.float32)
            return {
                "forward": True,
                "value": value,
                **{k: event[k] for k in ("stage", "reason")},
                "halt": False,
            }
        held = self.last_forwarded if kind == "joint_cmd" else None
        return {
            "forward": False,
            "value": held,
            "stage": stage,
            "reason": event["reason"],
            "halt": event["halt"],
        }


def main() -> None:  # pragma: no cover — dora runtime
    import pyarrow as pa

    from aisle.nodes.ik_trajectory import fk_tcp
    from aisle.scenes.pharmacy import MED_NAMES, load_physics, resolve_layout
    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    arm = os.environ.get("AISLE_SHIELD_ARM", "oracle_sim_shield").strip()
    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    physics = load_physics()
    profile = physics["embodiment"][embodiment]
    tray = resolve_layout(physics, embodiment)["tray"]
    key = hashlib.sha256(
        f"aisle-semantic-gateway:{os.environ.get('AISLE_SEEDS', '')}".encode()
    ).digest()
    gateway = Gateway(arm, key, list(MED_NAMES), tray, float(profile.get("gripper_grasp_cmd", 1.0)))
    n_arm = int(np.asarray(profile["home_qpos"]).shape[0]) - int(profile.get("gripper_dofs", 2))
    qpos: np.ndarray | None = None
    last_note: tuple | None = None  # (stage, reason) of the last logged hold

    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)

    def tcp_now() -> np.ndarray:
        if qpos is None:
            return np.array([np.inf, np.inf, np.inf])
        return np.asarray(fk_tcp(np.asarray(qpos[:n_arm], dtype=np.float64), embodiment))

    for event in node:
        if event["type"] != "INPUT":
            continue
        topic, metadata = event["id"], (event.get("metadata") or {})
        if not env_accepts(metadata, env_pin):
            continue
        now_s = int(metadata.get("sim_time_ns", 0)) / 1e9
        if topic == "poses":
            gateway.identity.on_poses(event["value"].to_numpy(zero_copy_only=False), now_s)
        elif topic == "joint_state":
            qpos = np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.float32)
        elif topic == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            gateway.on_goal(goal, metadata.get("goal_id", ""), now_s, list(MED_NAMES))
        elif topic == "episode_result":
            # SEM-8 evidence: every permit/refusal of the episode, with the
            # arm and goal, beside the run's results (the trace recorder
            # does not know this topic)
            results = os.environ.get("AISLE_RESULTS")
            if results:
                goal_id = gateway.assignment["episode_id"] if gateway.assignment else ""
                with (Path(results).parent / "shield_events.jsonl").open("a") as f:
                    for ev in gateway.events:
                        f.write(json.dumps({**ev, "arm": arm, "goal_id": goal_id}) + "\n")
            gateway.events = []
            gateway.assignment = None
        elif topic == "reset_done":
            gateway.on_reset()
        elif topic in ("joint_proposal", "gripper_proposal"):
            # proposals arrive on their own port names: VAL-5 reserves
            # joint_cmd/gripper_cmd inputs for guard-gated actuators
            kind = "joint_cmd" if topic == "joint_proposal" else "gripper_cmd"
            value = np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.float32)
            decision = gateway.propose(kind, value, tcp_now(), now_s)
            if decision["stage"] is not None:
                send(
                    "shield_event",
                    pa.array([json.dumps({**gateway.events[-1], "arm": arm})]),
                    {"sim_time_ns": metadata.get("sim_time_ns", 0)},
                )
            if decision["forward"]:
                send(kind, pa.array(value), metadata)
                continue
            if decision["value"] is not None:
                send(kind, pa.array(decision["value"]), metadata)  # hold position
            note = (decision["stage"], decision["reason"])
            if note != last_note:  # one line per hold, not one per command
                last_note = note
                print(f"semantic hold ({note[0]}): {note[1]}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    main()
