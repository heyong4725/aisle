"""Shared dataflow-test helpers: build a bridge dataflow YAML, run it under
`dora run --uv` with a hard kill, and read the recorder's JSONL."""

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_NODES = REPO_ROOT / "tests" / "fixtures" / "nodes"
BRIDGE = REPO_ROOT / "src" / "aisle" / "nodes" / "dora_genesis.py"
RESET_SERVICE = REPO_ROOT / "src" / "aisle" / "reset" / "service.py"
GUARD = REPO_ROOT / "src" / "aisle" / "nodes" / "budget_guard.py"

BRIDGE_OUTPUTS = [
    "bridge_info",
    "joint_state",
    "gripper_state",
    "oracle_state",
    "rgb_overhead",
    "rgb_wrist",
    "depth_overhead",
    "reset_done",
]
DRIVER_OUTPUTS = [
    "joint_cmd",
    "gripper_cmd",
    "reset",
    "episode_goal",
    "episode_feedback",
    "episode_result",
]


def _q(source: str) -> dict:
    """Extended input form: explicit queue (dora's default keeps only the
    latest message, which hides coalescing and evicts during long builds)."""
    return {"source": source, "queue_size": 100}


def write_bridge_dataflow(
    tmp_path: Path,
    record_out: Path,
    bridge_env: dict | None = None,
    driver_env: dict | None = None,
    duration_s: float = 10.0,
    with_verifier_stub: bool = False,
    with_reset_service: bool = False,
    with_guard: bool = False,
) -> Path:
    recorder_inputs = {t: f"bridge/{t}" for t in BRIDGE_OUTPUTS}
    if with_guard:
        recorder_inputs["violation"] = _q("budget-guard/violation")
        recorder_inputs["guard_stats"] = _q("budget-guard/guard_stats")
    if with_reset_service:
        # resets route THROUGH the dispatcher (RST-1); the recorder keeps the
        # bridge's own reset_done as a separate topic so send-side ordering
        # checks stay valid across the extra hop
        recorder_inputs["reset_done"] = "reset-service/reset_done"
        recorder_inputs["bridge_reset_done"] = "bridge/reset_done"
        # the request stream too: reset request arrival and reset_done
        # arrival share the recorder's clock, so their wall_t delta is a
        # true end-to-end RST-1 latency across all dispatcher hops
        recorder_inputs["reset"] = _q("driver/reset")
    if with_verifier_stub:
        recorder_inputs["episode_goal"] = "driver/episode_goal"
        recorder_inputs["episode_feedback"] = "verifier/episode_feedback"
        recorder_inputs["episode_result"] = "verifier/episode_result"
    dataflow = {
        "nodes": [
            {
                "id": "bridge",
                "path": str(BRIDGE),
                "inputs": {
                    "tick": "dora/timer/millis/10",
                    # explicit queues: dora's default keeps only the latest
                    # message, which hides coalescing (BRG-3) and can evict
                    # commands queued during the bridge's long startup
                    "joint_cmd": _q(
                        "budget-guard/joint_cmd_safe" if with_guard else "driver/joint_cmd"
                    ),
                    "gripper_cmd": _q(
                        "budget-guard/gripper_cmd_safe" if with_guard else "driver/gripper_cmd"
                    ),
                    "reset": _q(
                        "reset-service/bridge_reset" if with_reset_service else "driver/reset"
                    ),
                },
                "outputs": BRIDGE_OUTPUTS,
                "env": {k: str(v) for k, v in (bridge_env or {}).items()},
            },
            {
                "id": "driver",
                "path": str(FIXTURE_NODES / "driver.py"),
                "inputs": {"tick": "dora/timer/millis/50"},
                "outputs": DRIVER_OUTPUTS,
                "env": {k: str(v) for k, v in (driver_env or {}).items()},
            },
            *(
                [
                    {
                        "id": "budget-guard",
                        "path": str(GUARD),
                        "inputs": {
                            "joint_cmd": _q("driver/joint_cmd"),
                            "gripper_cmd": _q("driver/gripper_cmd"),
                            "reset_done": _q("bridge/reset_done"),
                        },
                        "outputs": [
                            "joint_cmd_safe",
                            "gripper_cmd_safe",
                            "violation",
                            "guard_stats",
                        ],
                    }
                ]
                if with_guard
                else []
            ),
            *(
                [
                    {
                        "id": "reset-service",
                        "path": str(RESET_SERVICE),
                        "inputs": {
                            "reset": _q("driver/reset"),
                            "reset_done": _q("bridge/reset_done"),
                        },
                        "outputs": ["bridge_reset", "reset_done"],
                    }
                ]
                if with_reset_service
                else []
            ),
            *(
                [
                    {
                        "id": "verifier",
                        "path": str(FIXTURE_NODES / "verifier_stub.py"),
                        "inputs": {
                            "episode_goal": "driver/episode_goal",
                            "oracle_state": "bridge/oracle_state",
                        },
                        "outputs": ["episode_feedback", "episode_result"],
                    }
                ]
                if with_verifier_stub
                else []
            ),
            {
                "id": "recorder",
                "path": str(FIXTURE_NODES / "recorder.py"),
                "inputs": recorder_inputs,
                "env": {
                    "RECORDER_OUT": str(record_out),
                    "RECORDER_DURATION_S": str(duration_s),
                },
            },
        ]
    }
    graph = tmp_path / "dataflow.yaml"
    graph.write_text(yaml.safe_dump(dataflow, sort_keys=False))
    return graph


_NODE_PATTERNS = (
    "dora_genesis.py",
    "fixtures/nodes/driver.py",
    "fixtures/nodes/recorder.py",
    "fixtures/nodes/verifier_stub.py",
    "reset/service.py",
    "nodes/budget_guard.py",
)


def _reap_orphan_nodes(graph_dir: Path) -> None:
    """dora spawns nodes via `uv run` OUTSIDE our process group, so killing
    `dora run` leaks them — each leaked genesis node burns ~50% of a core
    forever, and past leaks have silently strangled whole test sessions.
    Reap ONLY processes whose cwd is THIS run's unique dataflow directory
    (dora spawns nodes with cwd = the yaml's dir), so unrelated runs and
    developer dataflows are never touched."""
    for pattern in _NODE_PATTERNS:
        pgrep = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        for pid in pgrep.stdout.split():
            cwd = subprocess.run(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True
            )
            if f"n{graph_dir}" in cwd.stdout.splitlines():
                subprocess.run(["kill", "-9", pid], capture_output=True)


@dataclass
class DataflowRun:
    timed_out: bool
    returncode: int | None
    stdout: str
    stderr: str


def run_dataflow(graph: Path, timeout_s: float) -> DataflowRun:
    """Run the dataflow; the bridge never exits on its own, so a timeout
    kill of the whole process group is the NORMAL end of a capture run.
    Output collected up to the kill is preserved either way."""
    proc = subprocess.Popen(
        ["dora", "run", str(graph), "--uv"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        _reap_orphan_nodes(graph.parent)
        return DataflowRun(False, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        _reap_orphan_nodes(graph.parent)
        return DataflowRun(True, proc.returncode, stdout or "", stderr or "")


def read_records(record_out: Path) -> list[dict]:
    if not record_out.exists():  # recorder saw zero events
        return []
    records = []
    for line in record_out.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


@pytest.fixture
def dataflow():
    """Dataflow helpers as a fixture: immune to conftest-module name
    collisions across test directories (bare `from conftest import` resolves
    to whichever conftest hit sys.path first)."""
    from types import SimpleNamespace

    return SimpleNamespace(write=write_bridge_dataflow, run=run_dataflow, read=read_records)
