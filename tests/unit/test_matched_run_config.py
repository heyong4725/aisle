"""MON-8/MON-12/MON-13: hash-bound run dispatch uses the actual arm interfaces."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_typed_graph_preflight import _stage
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _config(tmp_path):
    stage, receipt = _stage(tmp_path)
    first = next(iter(receipt["hosts"].values()))
    host = json.loads(Path(first["config_path"]).read_text())
    config = {
        "schema_version": "aisle.matched-run-config.v1",
        "purpose": "expert_parity",
        "arm": "typed",
        "controller_root": str(ROOT),
        "participant_root": receipt["snapshot_record"]["participant_root"],
        "session_id": "fixture-session",
        "plan_id": "sha256:" + "a" * 64,
        "run_id": "matched-fixture",
        "development": {
            "schema_version": "aisle.matched-development.v1",
            "purpose": "expert_parity",
            "tier": "T1",
            "embodiment": "franka",
            "verifier": "oracle",
            "reset": "teleport",
            "seeds": [7],
            "run_ceiling": 1,
            "episode_ceiling": 1,
            "timeout_s": 30,
        },
        "runtime_record": host["launch"]["runtime_record"],
        "worker_adapter_sha256": host["launch"]["attestation"]["adapter"]["sha256"],
        "launch": {"stages": [{"root": str(stage), "stage_id": receipt["immutable_id"]}]},
    }
    return config, stage, receipt


def _write(tmp_path, config):
    path = tmp_path / "private/run.json"
    path.write_text(json.dumps(config))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_bound_typed_dispatch_passes_verified_stage_factory_to_rollout(tmp_path, monkeypatch):
    """MON-8/MON-12: the run entry selects real staged host inputs and retains session identity."""
    from aisle.harness import matched_run, rollout

    config, stage, receipt = _config(tmp_path)
    path, digest = _write(tmp_path, config)
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        assert kwargs["no_idea_gate"] is False
        assert kwargs["record_simulator_work"] is True
        assert kwargs["typed_stage_factory"](0) == (stage, receipt)
        assert (
            kwargs["graph"]
            == Path(receipt["snapshot_record"]["snapshot_root"]) / "graphs/expert_t1.yaml"
        )
        return {"ok": True, "campaign_purpose": "expert_parity"}

    monkeypatch.setattr(rollout, "rollout", run)
    result = matched_run.run_configured(path, digest)
    assert result["ok"], result
    assert len(calls) == 1
    assert result["matched_run"]["plan_id"] == config["plan_id"]
    assert result["matched_run"]["config_sha256"] == digest


@pytest.mark.parametrize("mutation", ["hash", "stage", "runtime", "purpose"])
def test_invalid_run_binding_refuses_before_rollout(tmp_path, monkeypatch, mutation):
    """MON-13: stale or unscored-purpose violations must not reach graph execution."""
    from aisle.harness import matched_run, rollout

    config, stage, receipt = _config(tmp_path)
    if mutation == "stage":
        config["launch"]["stages"][0]["stage_id"] = "0" * 64
    elif mutation == "runtime":
        config["runtime_record"]["immutable_id"] = "0" * 64
    elif mutation == "purpose":
        config["purpose"] = "confirmatory"
    path, digest = _write(tmp_path, config)
    if mutation == "hash":
        path.write_text(path.read_text() + " ")
    monkeypatch.setattr(rollout, "rollout", lambda **kw: pytest.fail("rollout started"))
    result = matched_run.run_configured(path, digest)
    assert not result["ok"]
    assert result["infrastructure_invalid"]


def test_missing_run_config_cli_returns_json_refusal(tmp_path):
    """CON-8/MON-13: a real controller child reports an invalid binding without dispatch."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.matched_run",
            "--config",
            str(tmp_path / "absent"),
            "--config-sha256",
            "0" * 64,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert not report["ok"]
    assert report["infrastructure_invalid"]


@pytest.mark.parametrize("drift", [False, True, "authored_failure"])
def test_monolithic_dispatch_uses_bound_worker_configuration(tmp_path, monkeypatch, drift):
    """MON-8/MON-13: the run interface receives a verified source and worker selection."""
    from test_monolith_worker_config import _config as worker_config
    from test_treatment_confinement import _attestation

    from aisle.harness import matched_run, monolith
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    config, _, _ = _config(tmp_path)
    view = Path(config["participant_root"])
    module = view / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    if drift == "authored_failure":
        module.write_text("API_VERSION='1.0'\nraise ValueError('authored failure')\n")
    mono = tmp_path / "mono"
    mono.mkdir()
    worker_path, _, _ = worker_config(mono, module)
    worker = json.loads(worker_path.read_text())
    launch = worker["launch"]
    launch["policy"]["hidden_roots"].extend([str(ROOT), str(view), str(tmp_path / "private")])
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    compiled = compile_macos_profile(policy)
    Path(launch["profile_path"]).write_text(compiled.text)
    launch["attestation"] = _attestation(
        compiled, Path(launch["profile_path"]), mono / "synthetic-adapter"
    )
    worker_path.write_text(json.dumps(worker))
    config.update(
        arm="monolithic",
        runtime_record=launch["runtime_record"],
        launch={
            "worker_config": str(worker_path),
            "worker_config_sha256": hashlib.sha256(worker_path.read_bytes()).hexdigest(),
        },
    )
    path, digest = _write(tmp_path, config)
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        assert kwargs["module"] == module
        assert kwargs["worker_config_sha256"] == config["launch"]["worker_config_sha256"]
        assert kwargs["no_idea_gate"] is False
        assert kwargs["record_simulator_work"] is True
        return {"ok": True, "campaign_purpose": "expert_parity"}

    if drift != "authored_failure":
        monkeypatch.setattr(monolith, "run", run)
    if drift is True:
        module.write_text("raise AssertionError('must never execute')")
    result = matched_run.run_configured(path, digest)
    assert result["ok"] is (drift is False), result
    assert len(calls) == (1 if drift is False else 0)
    if drift is True:
        assert result["infrastructure_invalid"]
    if drift == "authored_failure":
        assert not result.get("infrastructure_invalid"), result
        assert result["worker_evidence"]["ok"]
        assert "check/rpc/worker.json" in result["worker_evidence"]["files"]
        retained = path.parent / "run-controller/monolithic-worker"
        assert (retained / "module.py").read_bytes() == module.read_bytes()


def test_partial_run_config_cli_returns_json_refusal(tmp_path):
    """CON-8/MON-13: incomplete binding arguments cannot bypass the JSON refusal contract."""
    result = subprocess.run(
        [sys.executable, "-m", "aisle.harness.matched_run", "--config", str(tmp_path / "absent")],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["infrastructure_invalid"]


def test_worker_adapter_must_match_the_run_identity(tmp_path):
    """MON-8/MON-13: worker capabilities cannot select an adapter outside the run identity."""
    from aisle.harness.matched_run import _worker_binding

    config, _, receipt = _config(tmp_path)
    config["worker_adapter_sha256"] = "0" * 64
    host = json.loads(Path(next(iter(receipt["hosts"].values()))["config_path"]).read_text())
    with pytest.raises(ValueError, match="adapter"):
        _worker_binding(host["launch"], config, tmp_path / "private/run.json")
