"""MON-8/MON-13/TRT-6: actual Python worker file and executable authority."""

import errno
import json
import subprocess
import sys

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("operation", ["read", "write", "exec", "subprocess_read"])
def test_operation_denial_requires_matching_started_operation(operation):
    """TRT-6: inability to start the intended probe does not prove its operation denied."""
    from aisle.harness.worker_authority_probe import operation_case

    result = subprocess.CompletedProcess(
        ["fixture"],
        3,
        (
            json.dumps({"phase": "ready"})
            + "\n"
            + json.dumps(
                {
                    "phase": "error",
                    "operation": "startup",
                    "errno": errno.EPERM,
                }
            )
            + "\n"
        ).encode(),
        b"",
    )
    assert not operation_case(result, operation, b"test-sentinel", allowed=False)["passed"]


@pytest.mark.parametrize("failure", ["missing", "wrong_errno", "extra_row", "leak", "wrong_exit"])
def test_read_denial_rejects_inconclusive_or_leaking_results(failure):
    """TRT-6: missing targets, malformed records and sentinel leaks do not certify denial."""
    from aisle.harness.worker_authority_probe import operation_case

    sentinel = b"test-sentinel"
    rows = [{"phase": "ready"}, {"phase": "error", "operation": "read", "errno": errno.EPERM}]
    if failure == "missing":
        rows[1]["errno"] = errno.ENOENT
    elif failure == "wrong_errno":
        rows[1]["errno"] = True
    elif failure == "extra_row":
        rows.append({"phase": "unexpected"})
    result = subprocess.CompletedProcess(
        ["fixture"],
        0 if failure == "wrong_exit" else 3,
        ("".join(json.dumps(row) + "\n" for row in rows)).encode(),
        sentinel if failure == "leak" else b"",
    )
    assert not operation_case(result, "read", sentinel, allowed=False)["passed"]


@pytest.mark.skipif(sys.platform != "darwin", reason="actual sandbox-exec is macOS-only")
@pytest.mark.parametrize(
    "operation,visible",
    [
        ("read", True),
        ("read", False),
        ("subprocess_read", True),
        ("subprocess_read", False),
        ("write", True),
        ("write", False),
        ("exec", False),
    ],
)
def test_actual_python_profile_authority(tmp_path, operation, visible):
    """MON-8/TRT-6: baseline control precedes measured file/subprocess/exec restriction."""
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.worker_authority_probe import operation_case, operation_command
    from aisle.harness.worker_network_probe import _capture

    inputs = _launch_inputs(tmp_path)
    sentinel = b"controller-owned-probe-data"
    if operation == "exec":
        from pathlib import Path

        target = Path("/usr/bin/true")
    else:
        parent = (
            (inputs["policy"].output_roots[0] if operation == "write" else inputs["bundle"])
            if visible
            else tmp_path / "private"
        )
        target = parent / "probe-data"
        if operation != "write":
            target.write_bytes(sentinel)
    command = operation_command(inputs["python"], operation, target, sentinel)
    results = []
    for confined in (False, True):
        argv = (
            [str(SANDBOX_EXEC), "-f", str(inputs["profile_path"])] if confined else []
        ) + command
        result = _capture(
            argv,
            inputs["bundle"],
            inputs["environment"],
            inputs["environment_record"],
            tmp_path / "private" / ("confined" if confined else "baseline"),
        )
        case = operation_case(result, operation, sentinel, allowed=visible or not confined)
        assert case["passed"], (case, result.stdout, result.stderr)
        results.append(case)
        if operation == "write":
            if visible or not confined:
                assert target.read_bytes() == sentinel
                target.unlink()
            else:
                assert not target.exists()
    assert results[0]["operation_succeeded"]
    assert results[1]["operation_succeeded"] is visible
