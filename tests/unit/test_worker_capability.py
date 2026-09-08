"""MON-8/MON-13/TRT-6: actual per-worker profile capability aggregation."""

import json
import sys

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox capability")
def test_actual_worker_capability_report_and_cleanup(tmp_path):
    """TRT-6: all required observations bind to the actual policy and preserve worker assets."""
    from aisle.harness.treatment_confinement import (
        _REQUIRED_CASE_IDS,
        compile_macos_profile,
        wrap_verified_command,
    )
    from aisle.harness.worker_capability import audit_worker_capability

    inputs = _launch_inputs(tmp_path)
    visible_before = sorted(p.relative_to(inputs["bundle"]) for p in inputs["bundle"].rglob("*"))
    home = inputs["policy"].output_roots[0]
    home_before = sorted(p.relative_to(home) for p in home.rglob("*"))
    output = tmp_path / "private/capability"
    report = audit_worker_capability(
        policy=inputs["policy"],
        profile_path=inputs["profile_path"],
        python=inputs["python"],
        environment=inputs["environment"],
        environment_record=inputs["environment_record"],
        output=output,
    )
    assert report["capability_pass"], report
    assert report["confirmatory_ready"] is False
    assert len(report["cases"]) == len(_REQUIRED_CASE_IDS)
    assert {row["id"] for row in report["cases"]} == _REQUIRED_CASE_IDS
    assert all(row["passed"] for row in report["controls"])
    assert json.loads((output / "report.json").read_text()) == report
    assert (
        sorted(p.relative_to(inputs["bundle"]) for p in inputs["bundle"].rglob("*"))
        == visible_before
    )
    assert sorted(p.relative_to(home) for p in home.rglob("*")) == home_before
    for row in report["cases"]:
        assert (output / row["capture"] / "stdout.jsonl").is_file()
        assert (output / row["capture"] / "process.json").is_file()
    command = wrap_verified_command(
        [str(inputs["python"].resolve()), "-V"],
        compile_macos_profile(inputs["policy"]),
        inputs["profile_path"],
        report,
    )
    assert command[0] == "/usr/bin/sandbox-exec"


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox capability")
@pytest.mark.parametrize("failure", ["redirect", "network", "cancel", "setup", "cleanup"])
def test_failed_audit_retains_refusal_and_cleans_worker_roots(tmp_path, monkeypatch, failure):
    """MON-13/TRT-6: failed or redirected audit cannot retain passing capability evidence."""
    from aisle.harness import worker_capability as capability

    inputs = _launch_inputs(tmp_path)
    network = capability.probe_worker_network

    def probe(**kwargs):
        if failure == "cancel":
            raise KeyboardInterrupt()
        result = network(**kwargs)
        if failure == "network":
            result["ok"] = False
        elif failure == "redirect":
            profile = inputs["profile_path"]
            replacement = tmp_path / "replacement.sb"
            replacement.write_bytes(profile.read_bytes())
            profile.unlink()
            profile.symlink_to(replacement)
        return result

    monkeypatch.setattr(capability, "probe_worker_network", probe)
    if failure == "setup":

        def fail_setup(**kwargs):
            raise OSError("fixture setup refused")

        monkeypatch.setattr(capability, "_apple_git_runtime", fail_setup)
    elif failure == "cleanup":
        remove = capability.shutil.rmtree

        def fail_cleanup(path):
            remove(path)
            raise OSError("fixture cleanup uncertain")

        monkeypatch.setattr(capability.shutil, "rmtree", fail_cleanup)
    output = tmp_path / "private/capability"
    kwargs = dict(
        policy=inputs["policy"],
        profile_path=inputs["profile_path"],
        python=inputs["python"],
        environment=inputs["environment"],
        environment_record=inputs["environment_record"],
        output=output,
    )
    if failure == "cancel":
        with pytest.raises(KeyboardInterrupt):
            capability.audit_worker_capability(**kwargs)
        report = json.loads((output / "report.json").read_text())
    else:
        report = capability.audit_worker_capability(**kwargs)
    assert not report["capability_pass"]
    assert report["error"] or report["cleanup_errors"]
    assert json.loads((output / "report.json").read_text()) == report
    for root in (inputs["bundle"], inputs["policy"].output_roots[0]):
        assert not list(root.glob("aisle-capability-*"))
