"""MON-6/MON-12: a confined authored worker crosses the real Dora transport.

Engineering transport acceptance only; no simulator or study parity is claimed.
Run with the verified source installation's bin directory on PATH.
"""

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


def test_confined_worker_preserves_dora_values_and_turn_acknowledgement(tmp_path, monkeypatch):
    """MON-6/MON-12/CON-4: actual Dora retains payload, turn identity and host acknowledgement."""
    cli = shutil.which("dora")
    if cli is None:
        pytest.skip("source-pinned Dora CLI is unavailable")
    prefix = Path(cli).resolve().parents[1]
    if not (prefix / "aisle-dora-receipt.json").is_file():
        pytest.skip("Dora installation has no source-pin receipt")
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    import numpy
    import pyarrow as pa
    import yaml
    from dora_runtime import verify
    from test_monolith_worker_launch import _worker_interpreter
    from test_typed_worker_launch import _inputs

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.typed_execution_bundle import build_execution_bundle
    from aisle.harness.worker_declaration import provision_worker_declaration

    verified = verify(ROOT / "dora-runtime.json", prefix)
    assert verified["acceptance_ready"], verified
    base = tmp_path.resolve()

    source = dedent("""
    from aisle.turn_node import Node
    from datetime import datetime
    import pyarrow as pa
    node = Node()
    observed = False
    for event in node:
        if event['type'] == 'INPUT' and event['id'] == 'observation':
            assert event['value'].equals(pa.array([1.25, None], type=pa.float32()))
            assert event['metadata']['semantic'] == 'fixture'
            assert type(event['metadata']['timestamp']) is datetime
            assert event['metadata']['timestamp'].utcoffset().total_seconds() == 0
            observed = True
        if event['type'] == 'INPUT' and event['id'] == 'turn':
            assert observed
            assert event['value'].equals(pa.array([3], type=pa.uint64()))
            node.send_output('result', pa.array([2**64-1, None, 0], type=pa.uint64()), {'seq': 1})
            node.stop_after_turn()
    """)
    inputs = _inputs(base, source)
    packages = base / "codecs"
    packages.mkdir()
    for package in (numpy, pa):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    python, runtime_root = _worker_interpreter()
    runtime = capture_runtime((runtime_root, packages))
    launch = provision_worker_declaration(
        arm="typed",
        bundle=base / "actual-bundle",
        home=base / "actual-home",
        evidence=base / "private/declaration",
        hidden_roots=(*inputs["source_roots"], base / "private"),
        runtime_record=runtime,
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        timeout_s=15,
    )
    bundle = Path(launch["bundle"])
    bundle.rmdir()
    launch["bundle_manifest"] = build_execution_bundle(ROOT, base / "snapshot", bundle)
    launch["source_roots"] = inputs["source_roots"]
    config = {
        "schema_version": "aisle.typed-node-host.v1",
        "purpose": "expert_parity",
        "node_id": "worker",
        "module": "aisle.nodes.segmented_pose",
        "outputs": ["result"],
        "wall_outputs": [],
        "configuration": {"environment": {}, "arguments": []},
        "output": str(base / "private/execution"),
        "launch": launch,
    }
    raw = json.dumps(config, default=str).encode()
    path = base / "private/host.json"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (base / "host.py").write_text(
        "from aisle.harness.typed_node_host import main\nraise SystemExit(main())\n"
    )
    (base / "driver.py").write_text(
        dedent("""
    import json
    import time
    from pathlib import Path
    import pyarrow as pa
    from dora import Node
    node=Node(); sent=False; rows={}; deadline=time.monotonic()+25
    for event in node:
        if time.monotonic() > deadline: break
        if event['type'] != 'INPUT': continue
        topic=event['id']
        if topic=='tick' and not sent:
            node.send_output('observation', pa.array([1.25, None], type=pa.float32()), {
                'turn_epoch':2, 'turn_id':3, 'sim_time_ns':30, 'semantic':'fixture', 'seq':1,
            })
            node.send_output('turn', pa.array([3], type=pa.uint64()), {
                'turn_epoch':2, 'turn_id':3, 'sim_time_ns':30, 'target_node':'worker',
                'expected_inputs':['observation'], 'expected_counts':[1],
            })
            sent=True
        elif topic in ('result','done'):
            rows[topic]={'value':event['value'].to_pylist(),'metadata':dict(event['metadata'])}
            Path('observed.json').write_text(json.dumps(rows,default=str))
            if set(rows)=={'result','done'}: break
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
                    "result": "worker/result",
                    "done": "worker/turn_done",
                },
                "outputs": ["turn", "observation"],
            },
            {
                "id": "worker",
                "path": sys.executable,
                "args": shlex.join(
                    [str(base / "host.py"), "--config", str(path), "--config-sha256", digest]
                ),
                "inputs": {"turn": "driver/turn", "observation": "driver/observation"},
                "outputs": ["result", "turn_done"],
            },
        ]
    }
    (base / "graph.yaml").write_text(yaml.safe_dump(graph))
    with (base / "dora.log").open("w") as log:
        process = subprocess.Popen(
            [verified["binary"], "run", str(base / "graph.yaml")],
            cwd=base,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ),
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
    observed = json.loads((base / "observed.json").read_text())
    assert observed["result"]["value"] == [2**64 - 1, None, 0], observed
    for row in observed.values():
        assert (
            row["metadata"]["turn_id"] == 3
            and row["metadata"]["turn_epoch"] == 2
            and row["metadata"]["sim_time_ns"] == 30
        ), observed
    assert observed["done"]["metadata"]["emitted_counts"] == [1, 1], observed
    assert observed["done"]["metadata"]["shutdown"] is True, observed
    receipt = json.loads((base / "private/execution/host.json").read_text())
    assert receipt["ok"] and receipt["host_config_sha256"] == digest, receipt
