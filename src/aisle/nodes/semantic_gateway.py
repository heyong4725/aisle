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
- `sensor_shield`: OWLv2 on the overhead RGB frame (the VER-9 identity
  adapter), the detection nearest the tool centre point's projected pixel;
  refuses when no detection sits there or no frame/calibration exists.
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

#: a gripper command is a CLOSING proposal once it moves this fraction of the
#: way from the open value toward the grasp value; the executor ramps the
#: command in 0.01 steps, so gating only the final value would let the
#: fingers close through the ramp unauthorized (first live shakeout)
CLOSING_FRACTION = 0.1
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


SENSOR_SOURCE = "owlv2_overhead"
SENSOR_MIN_INTERVAL_S = 0.4  # sim seconds between detections while a stage is active
SENSOR_PIXEL_RADIUS = 60.0  # a detection whose box centre is within this of the TCP pixel


class SensorIdentity:
    """The deployable-shaped adapter: OWLv2 (VER-9 identity adapter) on the
    latest overhead RGB frame, the box nearest the tool centre point's
    projected pixel, scores normalized over the detections at that spot.
    Detection is CPU-bound (seconds per frame) so it runs only when an
    authorization-bearing proposal needs it and at most every
    SENSOR_MIN_INTERVAL_S of sim time; the assertion's capture_s is the
    frame's stamp, so the authorizer's max_age_s still applies."""

    def __init__(self, med_names: list[str], detector=None, projector=None):
        self.med_names = med_names
        self._detector = detector
        self._projector = projector
        self.calibration: dict | None = None
        self.rgb: np.ndarray | None = None
        self.stamp_s: float = -1.0
        self.n = 0
        self.source_hash = hashlib.sha256(SENSOR_SOURCE.encode()).hexdigest()
        self._cache: tuple[float, list[dict]] | None = None  # (stamp_s, detections)
        self._last_detect_s: float = -1e9
        self.detections_run = 0

    def on_calibration(self, calibration: dict) -> None:
        self.calibration = calibration

    def on_rgb(self, rgb: np.ndarray, sim_time_s: float) -> None:
        self.rgb, self.stamp_s = rgb, sim_time_s

    def _detect(self):
        if self._detector is None:
            from aisle.verifier.models import detect_meds, load_pinned

            pair = load_pinned("identity")
            self._detector = lambda rgb: detect_meds(rgb, list(self.med_names), model_pair=pair)
        return self._detector

    def _project(self, tcp: np.ndarray) -> np.ndarray:
        if self._projector is not None:
            return np.asarray(self._projector(tcp), dtype=np.float64)
        from aisle.verifier.stages import project_to_pixels

        return np.asarray(
            project_to_pixels(np.asarray([tcp], dtype=np.float64), self.calibration)[0]
        )

    def detections(self, now_s: float) -> list[dict] | None:
        """Cached detections for the latest frame; a new detection only when
        the frame is newer than the cache and the interval elapsed."""
        if self.rgb is None or self.calibration is None:
            return None
        if self._cache is not None and self._cache[0] == self.stamp_s:
            return self._cache[1]
        if now_s - self._last_detect_s < SENSOR_MIN_INTERVAL_S - 1e-9 and self._cache is not None:
            return self._cache[1]
        found = list(self._detect()(self.rgb))
        self.detections_run += 1
        self._last_detect_s = now_s
        self._cache = (self.stamp_s, found)
        return found

    def assertion(self, tcp: np.ndarray, radius: float, now_s: float) -> tuple[dict, str | None]:
        self.n += 1
        found = self.detections(now_s)
        refused, classes, track = True, {}, None
        capture_s = self._cache[0] if self._cache is not None else -1.0
        if found is not None and np.all(np.isfinite(tcp)):
            uv = self._project(tcp)
            near = []
            for d in found:
                x0, y0, x1, y1 = (float(v) for v in d["box"])
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if float(np.hypot(cx - uv[0], cy - uv[1])) <= SENSOR_PIXEL_RADIUS:
                    near.append(d)
            total = sum(float(d["score"]) for d in near)
            if near and total > 0:
                for d in near:
                    classes[d["label"]] = classes.get(d["label"], 0.0) + float(d["score"]) / total
                track = max(classes, key=classes.get)
                refused = False
        return {
            "assertion_id": f"sensor-{self.n:06d}",
            "source_hash": self.source_hash,
            "observation_id": f"rgb_overhead@{capture_s:.3f}",
            "track_id": track or "none",
            "carrier": "gripper" if not refused else None,
            "classes": classes,
            "refused": refused,
            "capture_s": capture_s,
            "receipt_s": now_s,
            "in_envelope": self.rgb is not None and self.calibration is not None,
            "evidence_kind": "rendered_perception",
        }, track


class Gateway:
    """Transport-free core, unit-testable without dora."""

    def __init__(
        self,
        arm: str,
        key: bytes,
        med_names: list[str],
        tray: dict,
        grasp_cmd: float,
        sensor: SensorIdentity | None = None,
        open_cmd: float = 0.0,
    ):
        if arm not in ARMS:
            raise ValueError(f"unknown shield arm {arm!r}; expected one of {ARMS}")
        self.arm = arm
        self.tray = tray
        self.grasp_cmd = grasp_cmd
        self.open_cmd = open_cmd
        if arm == "sensor_shield":
            self.identity = sensor if sensor is not None else SensorIdentity(med_names)
        else:
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

    def _closing(self, value) -> bool:
        span = self.grasp_cmd - self.open_cmd
        return (float(value[0]) - self.open_cmd) / span >= CLOSING_FRACTION if span else False

    def propose(self, kind: str, value: np.ndarray, tcp: np.ndarray, now_s: float) -> dict:
        """Decide one command. Returns {"forward": bool, "value": array|None,
        "stage": str|None, "reason": str|None, "halt": bool, "event": dict|None}.
        A refused closing command is replaced by the OPEN value (the fingers
        never close); a refused joint command re-sends the last forwarded
        one (the arm holds)."""
        closing_edge = False
        if kind == "gripper_cmd":
            closing = self._closing(value)
            if not closing:
                self.gripper_closed = False  # opening or open: always allowed
            closing_edge = closing and not self.gripper_closed
        stage = stage_of(self.gripper_closed, closing_edge, over_tray(tcp, self.tray))
        if stage is None or self.assignment is None:
            if kind == "joint_cmd":
                self.last_forwarded = np.asarray(value, dtype=np.float32)
            elif kind == "gripper_cmd" and self._closing(value):
                self.gripper_closed = True  # no assignment: nothing to authorize against
            return {
                "forward": True,
                "value": value,
                "stage": stage,
                "reason": None,
                "halt": False,
                "event": None,
            }
        radius = CANDIDATE_RADIUS_M if stage == "pre_grasp" else CARRY_RADIUS_M
        assertion, track = self.identity.assertion(tcp, radius, now_s)
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
                self.gripper_closed = True
            else:
                self.last_forwarded = np.asarray(value, dtype=np.float32)
            return {
                "forward": True,
                "value": value,
                **{k: event[k] for k in ("stage", "reason")},
                "halt": False,
                "event": event,
            }
        held = (
            self.last_forwarded
            if kind == "joint_cmd"
            else np.array([self.open_cmd], dtype=np.float32)  # refused closure: stay open
        )
        return {
            "forward": False,
            "value": held,
            "stage": stage,
            "reason": event["reason"],
            "halt": event["halt"],
            "event": event,
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
    gateway = Gateway(
        arm,
        key,
        list(MED_NAMES),
        tray,
        float(profile.get("gripper_grasp_cmd", 1.0)),
        open_cmd=float(profile.get("gripper_pregrasp_cmd", 0.0)),
    )
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
        if topic == "poses" and isinstance(gateway.identity, OracleIdentity):
            gateway.identity.on_poses(event["value"].to_numpy(zero_copy_only=False), now_s)
        elif topic == "bridge_info" and isinstance(gateway.identity, SensorIdentity):
            gateway.identity.on_calibration(json.loads(event["value"][0].as_py())["calibration"])
        elif topic == "rgb_overhead" and isinstance(gateway.identity, SensorIdentity):
            h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
            if h > 0 and w > 0:
                frame = np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.uint8)
                gateway.identity.on_rgb(frame.reshape(h, w, 3), now_s)
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
            if decision["event"] is not None:
                send(
                    "shield_event",
                    pa.array([json.dumps({**decision["event"], "arm": arm})]),
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
