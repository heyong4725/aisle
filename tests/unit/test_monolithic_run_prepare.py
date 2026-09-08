"""MON-3/MON-12/MON-13: construct worker inputs from the current single-file deliverable."""

import json
import shutil
from pathlib import Path

import pytest
from test_matched_worker_journal import _worker_inputs

pytestmark = pytest.mark.unit


def test_monolithic_preparation_uses_current_source_and_real_worker(tmp_path):
    """MON-3/MON-12: preparation does not replace ordinary authored failure with validation."""
    from aisle.harness.matched_evidence import audit_tool_journal

    controller, views, output, _, worker = _worker_inputs(tmp_path)
    declaration = worker["launch"]
    declaration.pop("bundle_manifest")
    declaration.pop("source_roots")
    bundle = Path(declaration["bundle"])
    shutil.rmtree(bundle)
    bundle.mkdir()
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.write_text("API_VERSION='1.0'\nraise ValueError('current edited source')\n")
    result = controller.run_with_workers(declaration)
    assert result["classification"] == "tool_result", result
    assert not result["ok"]
    assert "current edited source" in result["result"]["error"]
    assert result["result"]["worker_evidence"]["ok"]
    inputs = output / "tool-000001/monolithic-input"
    assert (inputs / "module.py").read_bytes() == module.read_bytes()
    config = json.loads((inputs / "worker-config.json").read_text())
    assert config["output_root"] == str(output / "tool-000001/monolithic-execution")
    assert result["artifacts"]["monolithic-input/worker-config.json"]
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm="monolithic",
        development=controller.plan["development"],
    )
    assert audit["ok"], audit


@pytest.mark.parametrize("fault", ["nonempty_bundle", "adapter"])
def test_monolithic_preparation_refuses_reuse_or_unadmitted_adapter(tmp_path, fault):
    """MON-13: preparation cannot overwrite bundle contents or choose a different adapter."""
    controller, views, output, _, worker = _worker_inputs(tmp_path)
    declaration = worker["launch"]
    declaration.pop("bundle_manifest")
    declaration.pop("source_roots")
    bundle = Path(declaration["bundle"])
    before = {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    if fault == "adapter":
        declaration["attestation"]["adapter"]["sha256"] = "0" * 64
    result = controller.run_with_workers(declaration)
    assert result["classification"] == "infrastructure_exclusion", result
    assert result["process"] is None
    assert ("not empty" if fault == "nonempty_bundle" else "adapter") in result["error"]
    assert not (output / "tool-000001/run-config.json").exists()
    assert {
        p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()
    } == before
