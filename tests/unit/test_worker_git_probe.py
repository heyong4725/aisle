"""MON-8/TRT-6: Python-only probes observe actual Git blob authority."""

import subprocess
import sys
import zlib

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "content", [b"tree 4\0test", b"blob 99\0test", b"blob 10000\0" + b"x" * 10000]
)
def test_invalid_git_object_is_not_success_or_denial(tmp_path, content):
    """TRT-6: corrupt, non-blob and oversized objects cannot be capability evidence."""
    from aisle.harness.worker_authority_probe import operation_case, operation_command

    target = tmp_path / "object"
    target.write_bytes(zlib.compress(content))
    result = subprocess.run(
        operation_command(sys.executable, "git_blob", target, b"test"),
        capture_output=True,
        timeout=5,
    )
    assert not operation_case(result, "git_blob", b"test", allowed=True)["passed"]
    assert not operation_case(result, "git_blob", b"test", allowed=False)["passed"]


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox and Apple Git")
@pytest.mark.parametrize("visible", [True, False])
def test_actual_git_object_read_under_worker_profile(tmp_path, visible):
    """MON-8/TRT-6: real committed blob can be read in baseline and obeys worker grants."""
    from aisle.harness.treatment_confinement import (
        SANDBOX_EXEC,
        _apple_git_runtime,
        _initialize_git_fixture,
    )
    from aisle.harness.worker_authority_probe import operation_case, operation_command
    from aisle.harness.worker_network_probe import _capture

    inputs = _launch_inputs(tmp_path)
    git, _ = _apple_git_runtime(cwd=tmp_path)
    environment = {
        "HOME": str(inputs["policy"].output_roots[0]),
        "PATH": str(git.parent),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    repository = (inputs["bundle"] if visible else tmp_path / "private") / "repository"
    sentinel = b"committed-controller-probe"
    oid = _initialize_git_fixture(repository, "data", sentinel, git=git, env=environment)
    target = repository / ".git/objects" / oid[:2] / oid[2:]
    # Remove the ordinary file: the probe must actually recover committed object bytes.
    (repository / "data").unlink()
    command = operation_command(inputs["python"], "git_blob", target, sentinel)
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
        case = operation_case(result, "git_blob", sentinel, allowed=visible or not confined)
        assert case["passed"], (case, result.stdout, result.stderr)


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS sandbox and Apple Git")
@pytest.mark.parametrize("route", ["symlink", "parent_traversal", "alternate_worktree"])
def test_hidden_file_routes_obey_actual_worker_profile(tmp_path, route):
    """MON-8/TRT-6: alternate file routes expose controls but remain denied under the profile."""
    from aisle.harness.treatment_confinement import (
        SANDBOX_EXEC,
        _apple_git_runtime,
        _controller_command,
        _initialize_git_fixture,
    )
    from aisle.harness.worker_authority_probe import operation_case, operation_command
    from aisle.harness.worker_network_probe import _capture

    inputs = _launch_inputs(tmp_path)
    sentinel = b"controller-hidden-route-probe"
    private = tmp_path / "private"
    if route == "alternate_worktree":
        git, _ = _apple_git_runtime(cwd=tmp_path)
        environment = {
            "HOME": str(inputs["policy"].output_roots[0]),
            "PATH": str(git.parent),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        repository = private / "repository"
        _initialize_git_fixture(repository, "data", sentinel, git=git, env=environment)
        worktree = private / "alternate"
        _controller_command(
            [str(git), "worktree", "add", "--quiet", "--detach", str(worktree), "HEAD"],
            cwd=repository,
            env=environment,
        )
        assert (worktree / ".git").is_file()
        target = worktree / "data"
    else:
        hidden = private / "data"
        hidden.write_bytes(sentinel)
        if route == "symlink":
            target = inputs["bundle"] / "probe-link"
            target.symlink_to(hidden)
        else:
            target = inputs["bundle"] / ".." / "private" / "data"
    command = operation_command(inputs["python"], "read", target, sentinel)
    assert command[-3] == str(target)  # Preserve the tested route, including '..'.
    for confined in (False, True):
        argv = (
            [str(SANDBOX_EXEC), "-f", str(inputs["profile_path"])] if confined else []
        ) + command
        result = _capture(
            argv,
            inputs["bundle"],
            inputs["environment"],
            inputs["environment_record"],
            private / ("confined" if confined else "baseline"),
        )
        case = operation_case(result, "read", sentinel, allowed=not confined)
        assert case["passed"], (case, result.stdout, result.stderr)
