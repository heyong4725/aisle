"""MON-6/MON-12/MON-13: Dora host selects only a hash-bound worker declaration."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from test_typed_node_requests import _node
from test_typed_worker_launch import _inputs

pytestmark = pytest.mark.unit


def _config(tmp_path):
    source = (
        "from aisle.turn_node import Node\n"
        "import pyarrow as pa\n"
        "node = Node()\n"
        "for event in node:\n"
        " node.send_output('result', pa.array([1.25], type=pa.float32()))\n"
    )
    inputs = _inputs(tmp_path, source)
    for name in ("node", "module", "outputs"):
        inputs.pop(name)
    output = inputs.pop("output")
    inputs["policy"] = asdict(inputs["policy"])
    config = {
        "schema_version": "aisle.typed-node-host.v1",
        "purpose": "expert_parity",
        "node_id": "worker",
        "module": "aisle.nodes.segmented_pose",
        "outputs": ["result"],
        "wall_outputs": [],
        "configuration": {"environment": {}, "arguments": []},
        "output": str(output),
        "launch": inputs,
    }
    path = tmp_path / "private/host.json"
    raw = json.dumps(
        config, default=lambda value: str(value) if isinstance(value, Path) else value
    ).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest(), config


def test_host_runs_worker_with_derived_turn_wrapper(tmp_path, monkeypatch):
    """MON-6: only the host owns Dora and turn acknowledgements despite ambient settings."""
    from aisle.harness.typed_node_host import run_configured_node

    path, digest, config = _config(tmp_path)
    raw, _ = _node()
    monkeypatch.setenv("AISLE_LOCKSTEP", "0")
    monkeypatch.setenv("AISLE_TURN_NODE", "wrong-host")
    result = run_configured_node(path, digest, raw_node_factory=lambda: raw)
    assert result["ok"], result
    done = [message for message in raw.sent if message[0] == "turn_done"]
    assert done
    assert raw.sent[0][0] == "result"
    assert raw.sent[0][2]["turn_id"] == 3
    receipt = json.loads((Path(config["output"]) / "host.json").read_text())
    assert receipt["host_config_sha256"] == digest
    assert receipt["result"] == result
    assert all(message[2]["source_node"] == config["node_id"] for message in done)


@pytest.mark.parametrize(
    "mutation", ["hash", "extra", "purpose", "output", "wall", "profile", "budget"]
)
def test_host_refuses_invalid_config_before_transport(tmp_path, mutation):
    """MON-13: stale or malformed host configuration cannot attach a Dora node."""
    from aisle.harness.typed_node_host import HostConfigError, run_configured_node

    path, digest, config = _config(tmp_path)
    if mutation == "hash":
        digest = "0" * 64
    elif mutation == "profile":
        Path(config["launch"]["profile_path"]).write_text("(allow default)")
    else:
        if mutation == "extra":
            config["build"] = "unexpected"
        elif mutation == "purpose":
            config["purpose"] = "confirmatory"
        elif mutation == "output":
            config["outputs"].append("turn_done")
        elif mutation == "budget":
            config["launch"]["timeout_s"] = -1
        else:
            config["wall_outputs"] = ["undeclared"]
        data = json.dumps(config, default=str).encode()
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
    calls = []
    with pytest.raises(HostConfigError):
        run_configured_node(path, digest, raw_node_factory=lambda: calls.append(True))
    assert not calls


def test_host_cli_returns_json_for_missing_configuration(tmp_path, capsys):
    """CON-8/MON-12: a host preflight failure is a JSON infrastructure verdict."""
    from aisle.harness.typed_node_host import main

    rc = main(["--config", str(tmp_path / "missing.json"), "--config-sha256", "0" * 64])
    result = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert result["ok"] is False
    assert result["classification"] == "infrastructure_exclusion"


def test_host_rejects_duplicate_config_keys(tmp_path):
    """MON-13: exact bytes cannot hide duplicate configuration declarations."""
    from aisle.harness.typed_node_host import HostConfigError, load_host_config

    path = tmp_path / "duplicate.json"
    raw = b'{"purpose":"expert_parity","purpose":"confirmatory"}'
    path.write_bytes(raw)
    with pytest.raises(HostConfigError, match="duplicate"):
        load_host_config(path, hashlib.sha256(raw).hexdigest())


def test_host_attaches_transport_only_after_worker_spawn(tmp_path, monkeypatch):
    """MON-12/MON-13: launcher preflight must not consume a ready Dora turn's deadline."""
    from aisle.harness import typed_worker_launch
    from aisle.harness.typed_node_host import run_configured_node

    path, digest, _ = _config(tmp_path)
    raw, _ = _node()
    spawn = typed_worker_launch.spawn_isolated_process
    children, attachments = [], []

    def observed_spawn(*args, **kwargs):
        process = spawn(*args, **kwargs)
        children.append(process)
        return process

    def attach():
        assert len(children) == 1, "Dora attached before worker launch checks and spawn"
        attachments.append(True)
        return raw

    monkeypatch.setattr(typed_worker_launch, "spawn_isolated_process", observed_spawn)
    result = run_configured_node(path, digest, raw_node_factory=attach)
    assert result["ok"], result
    assert attachments == [True]
    assert children[0].poll() == 0
    assert any(message[0] == "turn_done" for message in raw.sent)


def test_typed_transport_finishes_when_turn_input_closes_with_other_inputs_open():
    """MON-12/MON-13: coordinator closure ends the host stream without waiting on graph cycles."""
    from aisle.harness.typed_node_host import _DeferredTransport

    consumed = []

    def events():
        consumed.append("data_closed")
        yield {"type": "INPUT_CLOSED", "id": "joint_state"}
        consumed.append("turn_closed")
        yield {"type": "INPUT_CLOSED", "id": "turn"}
        pytest.fail("host waited on cyclic inputs after the coordinator closed")

    transport = _DeferredTransport(events)
    assert consumed == []
    assert list(transport) == [
        {"type": "INPUT_CLOSED", "id": "joint_state"},
        {"type": "INPUT_CLOSED", "id": "turn"},
    ]
    assert consumed == ["data_closed", "turn_closed"]
