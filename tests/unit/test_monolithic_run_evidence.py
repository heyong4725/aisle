"""MON-12/MON-13: retain worker precheck evidence without inventing simulation evidence."""

import json

import pytest
from test_monolith_worker_config import _config

pytestmark = pytest.mark.unit


def test_failed_worker_precheck_retains_source_and_raw_journal(tmp_path):
    """MON-12: normal authored failure survives collection with no run manifest."""
    from aisle.harness.monolith import run
    from aisle.harness.monolithic_run_evidence import retain_worker_attempt

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nraise ValueError('authored failure')\n")
    config, digest, worker_output = _config(tmp_path, module)
    destination = tmp_path / "private/retained"
    with retain_worker_attempt(config, digest, module, destination) as evidence:
        result = run(
            tmp_path,
            module,
            [7],
            1,
            run_id="failed-precheck",
            worker_config=config,
            worker_config_sha256=digest,
        )
    assert not result["ok"]
    assert not (tmp_path / "runs/failed-precheck/manifest.json").exists()
    assert evidence["ok"], evidence
    assert (destination / "module.py").read_bytes() == module.read_bytes()
    assert (destination / "worker-config.json").read_bytes() == config.read_bytes()
    assert "check/rpc/worker.json" in evidence["files"]
    assert (destination / "raw/check/rpc/worker.json").read_bytes() == (
        worker_output / "check/rpc/worker.json"
    ).read_bytes()
    assert json.loads((destination / "collection.json").read_text()) == evidence
