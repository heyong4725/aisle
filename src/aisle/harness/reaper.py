"""Orphan-node reaper shared by the rollout runner and the test harness.

dora spawns nodes via `uv run` OUTSIDE the launcher's process group, so
killing `dora run` leaks them; leaked genesis nodes burn ~50% of a core
each and have twice strangled whole sessions (T05, T08). Reap ONLY
processes whose cwd is the given unique run/dataflow directory. SIGTERM
first (the trace recorder must flush its writers), then SIGKILL."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

NODE_PATTERNS = (
    "dora_genesis.py",
    "fixtures/nodes/driver.py",
    "fixtures/nodes/recorder.py",
    "fixtures/nodes/base_driver.py",
    "fixtures/nodes/base_recorder.py",
    "fixtures/nodes/guard_mutex_driver.py",
    "fixtures/nodes/mock_base.py",
    "fixtures/nodes/nav_goal_injector.py",
    "nodes/nav_action.py",
    "fixtures/nodes/verifier_stub.py",
    "reset/service.py",
    "nodes/budget_guard.py",
    "nodes/ik_trajectory.py",
    "nodes/oracle_pose.py",
    "nodes/grasp_topdown.py",
    "nodes/task_state_machine.py",
    "harness/rollout_client.py",
    "harness/trace_recorder.py",
    "verifier/oracle.py",
)


def reap_orphans(cwd_dir: Path, patterns: tuple[str, ...] = NODE_PATTERNS) -> None:
    for sig in ("-TERM", "-9"):
        matched = False
        for pattern in patterns:
            pgrep = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
            for pid in pgrep.stdout.split():
                cwd = subprocess.run(
                    ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                    capture_output=True,
                    text=True,
                )
                if f"n{cwd_dir}" in cwd.stdout.splitlines():
                    subprocess.run(["kill", sig, pid], capture_output=True)
                    matched = True
        if sig == "-TERM" and matched:
            time.sleep(3.0)
