"""AISLE monolithic-arm expert for tier T1 (SPEC 440, MON-9 candidate).

One ordinary module against the frozen primitive API (docs/monolithic/
primitive-api.md). It reproduces the typed expert's decision logic — L1
pose estimate from the overhead segmentation + depth pair, a top-down or
front grasp, the staged pick-place trajectory streamed at the joint_state
cadence, and up to eight in-episode retries when no verdict lands within
three seconds of a finished plan — using the same primitives the typed
nodes call.

Authored by the AISLE maintainers from the monolithic-arm documentation
only; provenance and hashes ride in docs/monolithic/experts.json.
"""

API_VERSION = "1.0"

MAX_RETRIES = 8  # matches graphs/expert_t1.yaml AISLE_MAX_RETRIES
RETRY_GRACE_TICKS = 3  # matches task_state_machine.RETRY_GRACE_TICKS


class Controller:
    def __init__(self, primitives, log):
        self.p = primitives
        self.log = log
        self.session = primitives.pose_session()
        self.streamer = None
        self.goal = None
        self.retries = 0
        self.retry_due_tick = None
        self.violations = {}

    # -- episode lifecycle ------------------------------------------------

    def _request_pose(self):
        if not self.session.on_target_request({"target_med": self.goal["target_med"]}):
            self.log(f"unknown med {self.goal['target_med']!r}")

    def on_event(self, event):
        name, payload, t = event["name"], event["payload"], event["sim_time_ns"]
        if name == "bridge_info":
            self.session.on_bridge_info(payload)
        elif name == "episode_goal":
            self.goal = payload
            self.retries = 0
            self.retry_due_tick = None
            self.violations = {}
            self.streamer = None
            self._request_pose()
        elif name == "episode_result":
            self.goal = None
            self.retry_due_tick = None
        elif name == "reset_done":
            self.session.on_reset_done()
            self.streamer = None
        elif name == "violation":
            kind = str(payload.get("kind", "unknown"))
            self.violations[kind] = self.violations.get(kind, 0) + 1
        elif name in ("seg_overhead", "depth_overhead"):
            return self._on_frame(name, payload, t)
        elif name == "joint_state":
            return self._on_joint_state(payload)
        elif name == "tick":
            return self._on_tick(payload)
        return []

    # -- perception -> plan ----------------------------------------------

    def _on_frame(self, name, frame, t):
        try:
            if name == "depth_overhead":
                estimate = self.session.on_depth(t, frame)
            else:
                estimate = self.session.on_seg(t, frame)
        except Exception as exc:  # PoseRefused (TC-9): keep the request pending
            self.log(f"pose refused for {self.session.target}: {exc}")
            return []
        if estimate is None:
            return []
        if self.streamer is not None and not self.streamer.done:
            return []  # one plan at a time
        pose = list(estimate["pos"]) + [0.0, 0.0, 0.0, 1.0]
        grasp = self.p.plan_grasp(pose, estimate["target_med"], estimate["neighbours"])
        staged = self.p.staged_plan(grasp)
        if not staged.ok:
            self.log(f"grasp plan failed: {staged.error}")
            return []
        self.streamer = self.p.streamer(staged)
        self.log(f"plan ready: {len(staged.stages)} stages")
        return []

    # -- execution ---------------------------------------------------------

    def _on_joint_state(self, qpos):
        if self.streamer is None or self.streamer.done:
            return []
        full_cmd, grip, logs = self.streamer.step(qpos)
        for line in logs:
            self.log(line)
        actions = []
        if grip is not None:
            actions.append({"gripper_cmd": float(grip)})
        if full_cmd is not None:
            actions.append({"joint_cmd": full_cmd})
        if self.streamer.done:
            self.streamer = None
            if self.goal is not None and self.retries < MAX_RETRIES:
                self.retry_due_tick = self._ticks + RETRY_GRACE_TICKS
        return actions

    _ticks = 0

    def _on_tick(self, ticks):
        self._ticks = ticks
        if self.goal is None:
            return []
        if self.retry_due_tick is not None and ticks >= self.retry_due_tick:
            self.retry_due_tick = None
            self.retries += 1
            self._request_pose()
        feedback = {"t": ticks, "phase": "executing", "retries": self.retries}
        if self.violations:
            feedback["violations"] = dict(self.violations)
        return [{"feedback": feedback}]
