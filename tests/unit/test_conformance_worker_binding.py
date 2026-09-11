"""MON-13: qualified execution uses the worker fixture admitted before launch."""

import copy
import hashlib
import json

import pytest
from test_conformance_scenarios import scenario_proof

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize(
    "change",
    [
        None,
        "timeout",
        "runtime",
        "index",
        "missing",
        "unbound",
        "duplicate",
        "missing_preparation",
        "fixture_drift",
    ],
)
def test_prepared_worker_is_bound_to_original_fixture(tmp_path, arm, change):
    """MON-13: a retained declaration hash cannot authorize different worker grants."""
    from aisle.harness.frontend_qualification import verify_worker_fixture

    proof, _ = scenario_proof(tmp_path)
    proof["record"]["arm"] = arm
    runtime = {"schema_version": "aisle.matched-runtime.v1", "immutable_id": "runtime", "trees": {}}
    proof["admission"]["tool_runtime"] = runtime
    worker = {"timeout_s": 570, "runtime_record": runtime, "policy": {"hidden_roots": ["/hidden"]}}
    compact = copy.deepcopy(worker)
    compact["runtime_record"] = {"runtime_id": "runtime"}
    fixture = {"typed": [{"node": compact}], "monolithic": [compact]}
    declaration = [{"node": worker}] if arm == "typed" else worker
    artifacts = proof["artifacts"]
    name = "fixtures/worker-preparations.json"
    raw = json.dumps(fixture).encode()
    profile = json.loads(artifacts["conformance/profile.json"])
    if change != "unbound":
        profile["fixture_files"][name] = hashlib.sha256(raw).hexdigest()
        artifacts["conformance/inputs/" + name] = raw
    raw_profile = json.dumps(profile).encode()
    artifacts["conformance/profile.json"] = raw_profile
    for launch in proof["admission"]["launch_bindings"].values():
        launch["conformance_profile"]["sha256"] = hashlib.sha256(raw_profile).hexdigest()
    actual = copy.deepcopy(declaration)
    row = actual[0]["node"] if arm == "typed" else actual
    if change == "timeout":
        row["timeout_s"] = 60
    if change == "runtime":
        row["runtime_record"]["trees"] = {"/unbound": {}}
    raw = json.dumps(actual).encode()
    attempt = {
        "operation": "run",
        "worker_preparation": {
            "index": 1 if change == "index" else 0,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    artifacts["tool-000001/attempt.json"] = json.dumps(attempt).encode()
    if change != "missing":
        artifacts["tool-000001/worker-declaration.json"] = raw
    if change == "duplicate":
        artifacts["tool-000002/attempt.json"] = artifacts["tool-000001/attempt.json"]
        artifacts["tool-000002/worker-declaration.json"] = raw
    if change == "missing_preparation":
        attempt.pop("worker_preparation")
        attempt["process"] = {"rc": 0}
        artifacts["tool-000001/attempt.json"] = json.dumps(attempt).encode()
    if change == "fixture_drift":
        artifacts["conformance/inputs/" + name] += b" "
    if change is None:
        assert verify_worker_fixture(proof) == 1
    else:
        with pytest.raises(ValueError):
            verify_worker_fixture(proof)
