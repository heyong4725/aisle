"""MON-4/MON-6/MON-12: real Dora broker transport with a confined monolithic worker."""

import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS worker confinement"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_monolithic_broker_routes_real_dora_observations_through_confined_worker(
    tmp_path, monkeypatch
):
    """MON-4/MON-6/MON-12: source-bound worker outputs retain the trusted turn/goal envelope."""
    cli = shutil.which("dora")
    if cli is None:
        pytest.skip("source-pinned Dora CLI is unavailable")
    prefix = Path(cli).resolve().parents[1]
    if not (prefix / "aisle-dora-receipt.json").is_file():
        pytest.skip("Dora installation has no source-pin receipt")
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    import numpy
    import pyarrow
    import yaml
    from dora_runtime import verify
    from test_monolith_worker_launch import _worker_interpreter

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
    from aisle.harness.rollout import scrub_bringup_env
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.worker_declaration import provision_worker_declaration

    verified = verify(ROOT / "dora-runtime.json", prefix)
    assert verified["acceptance_ready"], verified
    base = tmp_path.resolve()
    private = base / "private"
    private.mkdir()
    views = {arm: base / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        dedent("""
        API_VERSION = '1.0'
        class Controller:
            def __init__(self, primitives, log):
                self.goal = None
            def on_event(self, event):
                if event['name'] == 'episode_goal':
                    self.goal = event['payload']['target_med']
                if event['name'] == 'joint_state':
                    assert self.goal == 'fixture-med'
                    assert event['payload'].tolist() == [1.25, 2.5]
                    assert event['goal_id'] == 'fixture-goal'
                    assert event['sim_time_ns'] == 30
                    return [{'gripper_cmd': 0.5}, {'feedback': {'phase': 'fixture'}}]
                return []
    """)
    )
    packages = base / "codecs"
    packages.mkdir()
    for package in (numpy, pyarrow):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    python, runtime_root = _worker_interpreter()
    runtime = capture_runtime((runtime_root, packages))
    adapter = hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest()
    declaration = provision_worker_declaration(
        arm="monolithic",
        bundle=base / "bundle",
        home=base / "home",
        evidence=private / "declaration",
        hidden_roots=(ROOT, *views.values(), private),
        runtime_record=runtime,
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=adapter,
        timeout_s=15,
    )
    preparation = prepare_monolithic_run(
        controller_root=ROOT,
        views=views,
        output=private / "preparation",
        declaration=declaration,
        runtime=runtime,
        adapter=adapter,
        embodiment="franka",
    )
    (base / "driver.py").write_text(
        dedent("""
        import json
        import time
        from pathlib import Path
        import pyarrow as pa
        from dora import Node
        node = Node()
        sent = False
        rows = {}
        deadline = time.monotonic() + 25
        for event in node:
            if time.monotonic() > deadline: break
            if event['type'] != 'INPUT': continue
            topic = event['id']
            if topic == 'tick' and not sent:
                stamp = {'turn_epoch': 2, 'turn_id': 3, 'sim_time_ns': 30, 'seq': 1}
                node.send_output('goal', pa.array([json.dumps({'target_med': 'fixture-med'})]),
                                 {**stamp, 'goal_id': 'fixture-goal'})
                node.send_output('joints', pa.array([1.25, 2.5], type=pa.float32()), stamp)
                node.send_output('turn', pa.array([3], type=pa.uint64()), {
                    **stamp, 'target_node': 'broker',
                    'expected_inputs': ['episode_goal', 'joint_state'], 'expected_counts': [1, 1],
                })
                sent = True
            elif topic in ('gripper', 'feedback', 'done'):
                rows[topic] = {'value': event['value'].to_pylist(),
                               'metadata': dict(event['metadata'])}
                Path('observed.json').write_text(json.dumps(rows, default=str))
                if set(rows) == {'gripper', 'feedback', 'done'}: break
    """)
    )
    graph = {
        "nodes": [
            {
                "id": "driver",
                "path": sys.executable,
                "args": shlex.join([str(base / "driver.py")]),
                "inputs": {
                    "tick": "dora/timer/millis/100",
                    "gripper": "broker/gripper_cmd",
                    "feedback": "broker/episode_feedback",
                    "done": "broker/turn_done",
                },
                "outputs": ["goal", "joints", "turn"],
            },
            {
                "id": "broker",
                "path": sys.executable,
                "args": "-m aisle.nodes.monolith_broker",
                "env": {
                    "AISLE_MONOLITH_MODULE": str(module),
                    "AISLE_MONOLITH_WORKER_CONFIG": preparation["worker_config"],
                    "AISLE_MONOLITH_WORKER_CONFIG_SHA256": preparation["worker_config_sha256"],
                    "AISLE_EMBODIMENT": "franka",
                    "AISLE_LOCKSTEP": "1",
                    "AISLE_TURN_NODE": "broker",
                    "AISLE_TURN_OUTPUTS": "gripper_cmd,episode_feedback,turn_done",
                },
                "inputs": {
                    "episode_goal": "driver/goal",
                    "joint_state": "driver/joints",
                    "turn": "driver/turn",
                },
                "outputs": ["gripper_cmd", "episode_feedback", "turn_done"],
            },
        ]
    }
    path = base / "graph.yaml"
    path.write_text(yaml.safe_dump(graph))
    with (base / "dora.log").open("w") as log:
        process = subprocess.Popen(
            [verified["binary"], "run", str(path)],
            cwd=base,
            env=scrub_bringup_env(dict(os.environ)),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            rc = process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        finally:
            from conftest import _reap_orphan_nodes

            _reap_orphan_nodes(base)
    assert rc == 0, (base / "dora.log").read_text()
    rows = json.loads((base / "observed.json").read_text())
    assert rows["gripper"]["value"] == [0.5], rows
    assert json.loads(rows["feedback"]["value"][0]) == {"phase": "fixture"}, rows
    assert rows["feedback"]["metadata"]["goal_id"] == "fixture-goal", rows
    for row in rows.values():
        assert row["metadata"]["turn_id"] == 3 and row["metadata"]["turn_epoch"] == 2, rows
        assert row["metadata"]["sim_time_ns"] == 30, rows
    assert rows["done"]["metadata"]["emitted_counts"] == [1, 1, 1], rows
    record = json.loads(
        (private / "preparation/monolithic-execution/run/rpc/worker.json").read_text()
    )
    assert record["state"] == "closed" and record["rc"] == 0, record
