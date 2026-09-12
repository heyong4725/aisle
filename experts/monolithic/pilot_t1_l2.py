"""Maintainer-derived paired T1 L2 pilot candidate (MON-4/BND-2/BND-3).

Uses the shared pinned RGB/depth estimator and robot primitives. Public goals
and reset notifications drive lifecycle; oracle verdicts are not observations.
Retries follow plan completion and the same fixed grace as the typed candidate.
Derived from the existing maintained T1 module and L2 implementation; this is
NOT an independently authored expert or a parity/feasibility attestation.
"""

API_VERSION = "1.0"

MAX_RETRIES = 8  # matches graphs/pilot_t1_l2_typed.yaml AISLE_MAX_RETRIES
RETRY_GRACE_TICKS = 3  # matches task_state_machine.RETRY_GRACE_TICKS


class Controller:
    def __init__(self, primitives, log):
        self.p = primitives
        self.log = log
        self.session = primitives.l2_pose_session()
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
        elif name == "reset_done":
            self.session.on_reset_done()
            self.streamer = None
            self.goal = None
            self.retry_due_tick = None
        elif name == "violation":
            kind = str(payload.get("kind", "unknown"))
            self.violations[kind] = self.violations.get(kind, 0) + 1
        elif name in ("rgb_overhead", "depth_overhead"):
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
                estimate = self.session.on_rgb(t, frame)
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
