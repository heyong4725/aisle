"""MON-12/MON-13: failed collection preserves readable evidence without following redirects."""

import json

import pytest
from test_monolith_worker_config import _config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("redirect", [False, True])
def test_worker_collection_survives_caller_failure_and_refuses_redirects(tmp_path, redirect):
    """MON-12/MON-13: caller errors propagate while collection preserves bounded evidence."""
    from aisle.harness.monolithic_run_evidence import retain_worker_attempt

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\n")
    config, digest, source = _config(tmp_path, module)
    destination = tmp_path / "private/retained"
    with pytest.raises(RuntimeError, match="caller failure"):
        with retain_worker_attempt(config, digest, module, destination) as report:
            source.mkdir()
            (source / "stderr.log").write_text("worker diagnostic\n")
            if redirect:
                (source / "redirect").symlink_to(module)
            raise RuntimeError("caller failure")
    assert report["ok"] is not redirect
    assert (destination / "raw/stderr.log").read_text() == "worker diagnostic\n"
    assert not (destination / "raw/redirect").exists()
    assert bool(report["errors"]) is redirect
    assert json.loads((destination / "collection.json").read_text()) == report
