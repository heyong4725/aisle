"""MON-8/MON-13/TRT-6: worker network denial requires an executed Python probe."""

import errno
import json
import subprocess
import sys

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "phase,code,ready,expected",
    [
        ("connect", errno.EPERM, True, True),
        ("connect", errno.ECONNREFUSED, True, False),
        ("startup", errno.EPERM, True, False),
        ("connect", errno.EPERM, False, False),
    ],
)
def test_connection_denial_requires_phase_and_readiness(phase, code, ready, expected):
    """MON-13/TRT-6: exec/import failure and ordinary connection failure are not network denial."""
    from aisle.harness.worker_network_probe import connection_case

    rows = ([{"phase": "ready"}] if ready else []) + [{"phase": phase, "errno": code}]
    result = subprocess.CompletedProcess(
        ["fixture"], 3, ("".join(json.dumps(row) + "\n" for row in rows)).encode(), b""
    )
    case = connection_case(result, b"sentinel", confined=True)
    assert case["passed"] is expected


@pytest.mark.parametrize("encoded", [False, True])
def test_denial_with_stderr_sentinel_exposure_fails(encoded):
    """TRT-6: a denial record cannot override observed sentinel leakage."""
    from aisle.harness.worker_network_probe import connection_case

    sentinel = b"private-test-sentinel"
    rows = [{"phase": "ready"}, {"phase": "connect", "errno": errno.EPERM}]
    result = subprocess.CompletedProcess(
        ["fixture"],
        3,
        ("".join(json.dumps(row) + "\n" for row in rows)).encode(),
        sentinel.hex().encode() if encoded else sentinel,
    )
    case = connection_case(result, sentinel, confined=True)
    assert case["sentinel_exposed"]
    assert not case["passed"]


def test_spawn_failure_retains_terminal_record(tmp_path, monkeypatch):
    """MON-13: failed probe launch leaves explicit evidence rather than a missing receipt."""
    from aisle.harness import worker_network_probe as probe

    def fail(*args, **kwargs):
        raise OSError("test launch refused")

    monkeypatch.setattr(probe, "spawn_isolated_process", fail)
    output = tmp_path / "capture"
    with pytest.raises(OSError, match="test launch refused"):
        probe._capture(["fixture"], tmp_path, {}, {}, output)
    record = json.loads((output / "process.json").read_text())
    assert record["started"] is False
    assert record["returncode"] is None
    assert record["error"] == "test launch refused"


@pytest.mark.parametrize("cancelled", [False, True])
def test_interrupted_capture_reaps_child_and_retains_error(tmp_path, monkeypatch, cancelled):
    """MON-13: timeout and cancellation kill/reap the launched probe and keep terminal evidence."""
    import signal

    from aisle.harness import worker_network_probe as probe

    error = KeyboardInterrupt() if cancelled else subprocess.TimeoutExpired("fixture", 5)
    calls = []

    class Process:
        pid = 12345
        returncode = None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if timeout is not None:
                raise error
            self.returncode = -signal.SIGKILL

    process = Process()
    monkeypatch.setattr(probe, "spawn_isolated_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(probe.os, "killpg", lambda pid, sig: calls.append(("kill", pid, sig)))
    output = tmp_path / "capture"
    with pytest.raises(type(error)):
        probe._capture(["fixture"], tmp_path, {}, {}, output)
    assert calls == [("wait", 5), ("kill", process.pid, signal.SIGKILL), ("wait", None)]
    record = json.loads((output / "process.json").read_text())
    assert record["started"] is True
    assert record["returncode"] == -signal.SIGKILL
    assert record["error"] == (str(error) or type(error).__name__)


@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS profile fixture")
def test_listener_failure_prevents_success(tmp_path, monkeypatch):
    """TRT-6: controller socket failure invalidates otherwise successful probe records."""
    from aisle.harness import worker_network_probe as probe

    class FailedListener:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            pass

        def listen(self):
            pass

        def settimeout(self, timeout):
            pass

        def getsockname(self):
            return ("127.0.0.1", 12345)

        def accept(self):
            raise OSError("test listener failed")

    sentinel = b"test-listener-sentinel"

    def capture(argv, *args):
        confined = argv[0] == str(probe.SANDBOX_EXEC)
        final = (
            {"phase": "connect", "errno": errno.EPERM}
            if confined
            else {"phase": "read", "data": sentinel.hex()}
        )
        rows = [{"phase": "ready"}, final]
        return subprocess.CompletedProcess(
            argv,
            3 if confined else 0,
            ("".join(json.dumps(row) + "\n" for row in rows)).encode(),
            b"",
        )

    inputs = _launch_inputs(tmp_path, direct_python=True)
    monkeypatch.setattr(probe.socket, "socket", lambda *args: FailedListener())
    monkeypatch.setattr(probe, "_capture", capture)
    output = tmp_path / "private/network-probe"
    report = probe.probe_worker_network(
        policy=inputs["policy"],
        profile_path=inputs["profile_path"],
        python=inputs["python"],
        environment=inputs["environment"],
        environment_record=inputs["environment_record"],
        cwd=inputs["bundle"],
        output=output,
        sentinel=sentinel,
    )
    assert report["ok"] is False
    assert report["listener_errors"] == ["test listener failed"]
    assert json.loads((output / "report.json").read_text()) == report


@pytest.mark.skipif(sys.platform != "darwin", reason="actual sandbox-exec probe is macOS-only")
def test_python_only_profile_blocks_connection_after_probe_starts(tmp_path):
    """MON-8/TRT-6: positive host control and confined Python run test actual socket authority."""
    from aisle.harness.worker_network_probe import probe_worker_network

    inputs = _launch_inputs(tmp_path, direct_python=True)
    report = probe_worker_network(
        policy=inputs["policy"],
        profile_path=inputs["profile_path"],
        python=inputs["python"],
        environment=inputs["environment"],
        environment_record=inputs["environment_record"],
        cwd=inputs["bundle"],
        output=tmp_path / "private/network-probe",
        sentinel=b"worker-network-control",
    )
    assert report["ok"], report
    assert report["confirmatory_ready"] is False
    assert report["cases"][0]["sentinel_exposed"]
    assert report["cases"][1]["network_attempted"]
    assert report["cases"][1]["passed"]
    assert (tmp_path / "private/network-probe/confined/stdout.jsonl").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="actual sandbox-exec probe is macOS-only")
def test_python_only_profile_blocks_unix_socket_with_matching_control(tmp_path):
    """TRT-5/TRT-6/TRT-7: worker socket denial preserves its Python-only authority."""
    from aisle.harness.worker_network_probe import probe_worker_network

    inputs = _launch_inputs(tmp_path, direct_python=True)
    profile_before = inputs["profile_path"].read_bytes()
    report = probe_worker_network(
        policy=inputs["policy"],
        profile_path=inputs["profile_path"],
        python=inputs["python"],
        environment=inputs["environment"],
        environment_record=inputs["environment_record"],
        cwd=inputs["bundle"],
        output=tmp_path / "private/unix-probe",
        sentinel=b"worker-unix-control",
        transport="unix",
    )
    assert report["ok"], report
    cases = {row["id"]: row for row in report["cases"]}
    for name in ("unrestricted_unix_socket_baseline", "unix_socket_network_control"):
        assert cases[name]["sentinel_exposed"] and cases[name]["passed"]
    assert cases["unix_socket_read"]["network_attempted"]
    assert cases["unix_socket_read"]["denied"] and cases["unix_socket_read"]["passed"]
    assert not cases["unix_socket_read"]["sentinel_exposed"]
    assert inputs["profile_path"].read_bytes() == profile_before
    assert report["confirmatory_ready"] is False
    assert not list(inputs["bundle"].glob("aisle-unix-*.sock"))
    for capture in ("baseline", "confined", "network-control"):
        assert (tmp_path / "private/unix-probe" / capture / "process.json").exists()
