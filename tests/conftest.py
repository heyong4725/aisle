"""Shared dataflow-test helpers: build a bridge dataflow YAML, run it under
`dora run --uv` with a hard kill, and read the recorder's JSONL."""

import json
import os
import signal
import subprocess
import time
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
    "poses",
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
    recorder_await: str | None = None,
    recorder_await_tail_s: float | None = None,
    recorder_await_sim_ns: int | None = None,
    with_verifier_stub: bool = False,
    with_reset_service: bool = False,
    with_guard: bool = False,
    driver_waits_for_bridge_info: bool = False,
    step_without_reset: bool = True,
) -> Path:
    """step_without_reset defaults True: most fixture drivers never send a
    reset, and without the opt-out the ADR-25 bridge holds at sim step 0
    forever — the test then fails 420 s later, opaquely. Tests that exercise
    the production (reset-anchored) startup pass step_without_reset=False.

    recorder_await is "topic:count" (e.g. "reset_done:2", issue #94): the
    capture window may not close before the Nth row of the topic, which then
    re-anchors the deadline to now + recorder_await_tail_s (default: the
    duration); recorder_await_sim_ns additionally holds the window open until
    the recorded sim stamps advance that far past the Nth row's stamp.
    Misconfiguration is rejected HERE, in the pytest process, because a
    recorder waiting on a topic it can never see burns the settle helper's
    whole outer deadline (PR #159 review)."""
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
        # ADR-34: refusals answer here, not on the boundary. The recorder is
        # this fixture graph's requester-side consumer; without an edge dora
        # would drop the refusal and a refusal test would hang rather than fail
        recorder_inputs["reset_refused"] = "reset-service/reset_refused"
        # the request stream too: reset request arrival and reset_done
        # arrival share the recorder's clock, so their wall_t delta is a
        # true end-to-end RST-1 latency across all dispatcher hops
        recorder_inputs["reset"] = _q("driver/reset")
    if with_verifier_stub:
        recorder_inputs["episode_goal"] = "driver/episode_goal"
        recorder_inputs["episode_feedback"] = "verifier/episode_feedback"
        recorder_inputs["episode_result"] = "verifier/episode_result"
    if (recorder_await_tail_s is not None or recorder_await_sim_ns is not None) and (
        not recorder_await
    ):
        raise ValueError("recorder_await_tail_s/_sim_ns without recorder_await is a silent no-op")
    if recorder_await:
        topic, _, count = recorder_await.partition(":")
        if not topic or not count.isdigit() or int(count) < 1:
            raise ValueError(f"recorder_await must be 'topic:count', got {recorder_await!r}")
        if topic not in recorder_inputs:
            raise ValueError(
                f"recorder_await topic {topic!r} is not wired to the recorder "
                f"(inputs: {sorted(recorder_inputs)}) — the await could never be met"
            )
        # the awaited stream must be LOSSLESS: dora's default latest-wins
        # delivery can coalesce two awaited rows under recorder starvation,
        # leaving the count unreachable forever (PR #159 cross-model review)
        raw = recorder_inputs[topic]
        recorder_inputs[topic] = _q(raw if isinstance(raw, str) else raw["source"])
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
                "env": {
                    **({"AISLE_STEP_WITHOUT_RESET": "1"} if step_without_reset else {}),
                    **{k: str(v) for k, v in (bridge_env or {}).items()},
                },
            },
            {
                "id": "driver",
                "path": str(FIXTURE_NODES / "driver.py"),
                "inputs": {
                    "tick": "dora/timer/millis/50",
                    # always wired; a non-waiting driver just skips the event.
                    # Lets driver_waits_for_bridge_info time sends from the
                    # moment the bridge loop is live instead of process start
                    # (requests sent during the genesis build just queue up)
                    "bridge_info": "bridge/bridge_info",
                },
                "outputs": DRIVER_OUTPUTS,
                "env": {
                    **{k: str(v) for k, v in (driver_env or {}).items()},
                    **({"DRIVER_WAIT_BRIDGE_INFO": "1"} if driver_waits_for_bridge_info else {}),
                },
            },
            *(
                [
                    {
                        "id": "budget-guard",
                        "path": str(GUARD),
                        "inputs": {
                            "tick": "dora/timer/millis/5000",
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
                        # `reset_refused` is declared and consumed (ADR-34):
                        # dora silently DROPS a send_output to an undeclared
                        # output, so a fixture missing it would turn a
                        # refusal into a hang rather than a failure
                        "outputs": ["bridge_reset", "reset_done", "reset_refused"],
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
                    # "topic:count" — the window may not close before the
                    # Nth row of the topic, and that row re-anchors the
                    # deadline (issue #94: wall-only windows truncate
                    # mid-protocol under suite load)
                    **({"RECORDER_AWAIT": recorder_await} if recorder_await else {}),
                    **(
                        {"RECORDER_AWAIT_TAIL_S": str(recorder_await_tail_s)}
                        if recorder_await_tail_s is not None
                        else {}
                    ),
                    **(
                        {"RECORDER_AWAIT_SIM_NS": str(recorder_await_sim_ns)}
                        if recorder_await_sim_ns is not None
                        else {}
                    ),
                },
            },
        ]
    }
    graph = tmp_path / "dataflow.yaml"
    graph.write_text(yaml.safe_dump(dataflow, sort_keys=False))
    return graph


def _reap_orphan_nodes(graph_dir: Path) -> None:
    """Shared reaper (src/aisle/harness/reaper.py) scoped to THIS run's
    unique dataflow directory."""
    from aisle.harness.reaper import reap_orphans

    reap_orphans(graph_dir)


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


def run_dataflow_until_settled(graph: Path, record_out: Path, deadline_s: float) -> None:
    """Launch the dataflow and stop as soon as the duration-aware recorder
    writes its explicit `__recorder_done__` sentinel (its window elapsed with
    the stream flowing), then kill the group and reap. Unlike run_dataflow's
    fixed window the wall time is (genesis build + capture window), NOT the
    whole deadline: the bridge never self-exits, so a fixed timeout would
    always elapse.

    The sentinel is written only when an event arrives AFTER the window, i.e.
    the stream flowed through the whole window. A mid-capture STALL therefore
    leaves no sentinel; this helper then RAISES on hitting `deadline_s` rather
    than returning partial data — a stalled/truncated capture fails loudly, it
    does not pass on a pre-stall slice. `deadline_s` is the generous outer cap
    for a slow genesis build."""
    proc = subprocess.Popen(
        ["dora", "run", str(graph), "--uv"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    def _sentinel_written() -> bool:
        if not record_out.exists():
            return False
        return any(
            '"__recorder_done__"' in line for line in record_out.read_text().splitlines()[-3:]
        )

    settled = False
    died_early = False
    stderr_tail = ""
    try:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            time.sleep(2.0)
            if _sentinel_written():
                settled = True
                break
            if proc.poll() is not None:
                # the dataflow died (a node crashed, e.g. a malformed
                # recorder config): fail NOW with its stderr instead of
                # burning the whole deadline on an opaque no-sentinel
                # timeout (PR #159 review)
                died_early = True
                break
    finally:
        try:
            # unconditional: the leader may be dead while group members
            # (other nodes) live on; the zombie leader keeps the pgid valid
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # the whole group is already gone
        except PermissionError:
            # A completed dora leader can leave a process-group id that the
            # managed test sandbox will not signal. Terminate the owned leader
            # directly; the run-scoped orphan reaper below handles children.
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            _, err = proc.communicate()
        stderr_tail = (err or "")[-600:]
        _reap_orphan_nodes(graph.parent)
    if died_early:
        raise AssertionError(
            f"the dataflow exited (rc={proc.returncode}) before the recorder settled — "
            f"a node likely crashed; stderr tail:\n{stderr_tail}"
        )
    if not settled:
        raise AssertionError(
            f"recorder never wrote its completion sentinel within {deadline_s}s: the "
            "capture stalled (stream stopped mid-window), the build never finished, "
            "or a RECORDER_AWAIT condition was never met "
            f"— NOT a completed window, so the run is not accepted; stderr tail:\n{stderr_tail}"
        )


def read_records(record_out: Path) -> list[dict]:
    if not record_out.exists():  # recorder saw zero events
        return []
    records = []
    for line in record_out.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    # the recorder's completion sentinel is control-plane, not data: drop it
    # so consumers never see a pseudo-record without value/metadata
    return [r for r in records if r.get("id") != "__recorder_done__"]


@pytest.fixture
def dataflow():
    """Dataflow helpers as a fixture: immune to conftest-module name
    collisions across test directories (bare `from conftest import` resolves
    to whichever conftest hit sys.path first)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        write=write_bridge_dataflow,
        run=run_dataflow,
        run_until_settled=run_dataflow_until_settled,
        read=read_records,
    )
