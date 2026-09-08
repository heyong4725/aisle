"""MON-8/MON-13: fresh worker declarations use observed actual-profile capabilities."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from test_monolith_worker_launch import _launch_inputs, _worker_interpreter

pytestmark = pytest.mark.unit


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox")
@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_provisioned_declaration_uses_actual_adapter_and_fresh_roots(tmp_path, arm):
    """MON-8/TRT-6: provision runtime-bound worker grants without supplied capability flags."""
    from aisle.harness.treatment_confinement import SANDBOX_EXEC, MacOSPolicy, compile_macos_profile
    from aisle.harness.worker_declaration import provision_worker_declaration

    inputs = _launch_inputs(tmp_path, direct_python=True)
    kwargs = dict(
        arm=arm,
        bundle=tmp_path / "new-bundle",
        home=tmp_path / "new-home",
        evidence=tmp_path / "private/new-worker",
        hidden_roots=inputs["policy"].hidden_roots,
        runtime_record=inputs["runtime_record"],
        python=inputs["python"],
        python_sha256=hashlib.sha256(inputs["python"].read_bytes()).hexdigest(),
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        timeout_s=5,
    )
    launch = provision_worker_declaration(**kwargs)
    assert launch["python"] == str(inputs["python"].resolve())
    assert launch["attestation"]["capability_pass"]
    assert launch["attestation"]["adapter"]["path"] == str(SANDBOX_EXEC)
    assert "bundle_manifest" not in launch and "source_roots" not in launch
    assert not list(Path(launch["bundle"]).iterdir())
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    assert (
        compile_macos_profile(policy).sha256
        == launch["attestation"]["adapter"]["compiled_profile_sha256"]
    )
    assert json.loads((kwargs["evidence"] / "declaration.json").read_text()) == launch
    with pytest.raises(ValueError, match="fresh"):
        provision_worker_declaration(**kwargs)


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox")
def test_generated_monolithic_declaration_prepares_and_runs_real_worker(tmp_path):
    """MON-8/MON-12: generated capability feeds source preparation and actual confined RPC."""
    import shutil

    import numpy
    import pyarrow

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.worker_declaration import provision_worker_declaration
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.worker_config import configured_worker_factory

    inputs = _launch_inputs(tmp_path, direct_python=True)
    packages = tmp_path / "codec-runtime"
    packages.mkdir()
    for package in (numpy, pyarrow):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    _, runtime_root = _worker_interpreter()
    runtime = capture_runtime((runtime_root, packages))
    controller = Path(__file__).resolve().parents[2]
    participant = tmp_path / "participant-source"
    views = {arm: participant / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    source = (
        "API_VERSION='1.0'\nclass Controller:\n"
        " def __init__(self,p,log): pass\n def on_event(self,e): return []\n"
    )
    module.write_text(source)
    adapter = hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest()
    declaration = provision_worker_declaration(
        arm="monolithic",
        bundle=tmp_path / "generated-bundle",
        home=tmp_path / "generated-home",
        evidence=tmp_path / "private/generated-worker",
        hidden_roots=(controller, participant, tmp_path / "private"),
        runtime_record=runtime,
        python=inputs["python"],
        python_sha256=hashlib.sha256(inputs["python"].read_bytes()).hexdigest(),
        adapter_sha256=adapter,
        timeout_s=5,
    )
    output = tmp_path / "private/preparation"
    result = prepare_monolithic_run(
        controller_root=controller,
        views=views,
        output=output,
        declaration=declaration,
        runtime=runtime,
        adapter=adapter,
        embodiment="franka",
    )
    factory = configured_worker_factory(
        result["worker_config"], result["worker_config_sha256"], phase="check"
    )
    with factory(Primitives._load("franka")) as worker:
        worker.initialize(source, "fixture.py")
        assert worker.event({"name": "tick", "payload": 1}) == []
    receipt = json.loads((output / "monolithic-execution/check/launch.json").read_text())
    assert receipt["python_sha256"] == declaration["python_sha256"]
    assert (
        json.loads((output / "monolithic-execution/check/rpc/worker.json").read_text())["state"]
        == "closed"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox")
def test_generated_typed_declaration_runs_confined_arrow_turn(tmp_path):
    """MON-6/MON-12: actual confinement preserves Arrow transport and host turn ownership."""
    import shutil

    import numpy
    import pyarrow as pa
    from test_typed_node_requests import _node
    from test_typed_worker_launch import _inputs

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_confinement import SANDBOX_EXEC, MacOSPolicy
    from aisle.harness.typed_execution_bundle import build_execution_bundle
    from aisle.harness.typed_worker_launch import launch_typed_worker
    from aisle.harness.worker_declaration import provision_worker_declaration

    controller = Path(__file__).resolve().parents[2]
    source = f"""
from pathlib import Path
import pyarrow as pa
from aisle.turn_node import Node
try:
    Path({str(controller / "CLAUDE.md")!r}).read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('controller source was readable')
node = Node()
for event in node:
    assert event['value'].equals(pa.array([3], type=pa.uint64()))
    node.send_output('result', pa.array([2**64 - 1, None, 0], type=pa.uint64()), {{'seq': 1}})
"""
    inputs = _inputs(tmp_path, source)
    packages = tmp_path / "codecs"
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
        bundle=tmp_path / "actual-bundle",
        home=tmp_path / "actual-home",
        evidence=tmp_path / "private/actual-declaration",
        hidden_roots=(*inputs["source_roots"], tmp_path / "private"),
        runtime_record=runtime,
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        # Functional confinement/transport test; allow cold runtime verification in CI.
        timeout_s=30,
    )
    bundle = Path(launch["bundle"])
    bundle.rmdir()  # Provisioning reserves an empty bundle; capture owns its contents.
    manifest = build_execution_bundle(controller, tmp_path / "snapshot", bundle)
    launch["policy"] = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    raw, node = _node()
    output = tmp_path / "private/actual-execution"
    result = launch_typed_worker(
        **launch,
        bundle_manifest=manifest,
        source_roots=inputs["source_roots"],
        output=output,
        node=node,
        module="aisle.nodes.segmented_pose",
        outputs={"result"},
    )
    assert result["ok"], result
    assert result["worker"]["input_exhausted"]
    assert result["worker"]["rc"] == 0
    assert [row[0] for row in raw.sent] == ["result", "turn_done"]
    assert raw.sent[0][1].equals(pa.array([2**64 - 1, None, 0], type=pa.uint64()))
    assert raw.sent[0][2]["turn_id"] == 3
    assert raw.sent[1][2]["emitted_counts"] == [1, 1]
    assert json.loads((output / "result.json").read_text()) == result
