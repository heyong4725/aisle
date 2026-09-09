"""CON-9/CON-12: interrupted graph captures must stop their owned processes."""

import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("times_out", [False, True])
def test_normal_capture_and_timeout_keep_streams_and_reap(
    tmp_path, monkeypatch, dataflow, times_out
):
    """CON-9/CON-12: normal and timeout captures retain their existing result contract."""
    graph = tmp_path / "dataflow.yaml"
    graph.write_text("fixture graph")
    ready = tmp_path / "ready"
    children, reaped = [], []

    def launch(*args, **kwargs):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time; from pathlib import Path; "
                "print('out',flush=True); print('err',file=sys.stderr,flush=True); "
                f"Path({str(ready)!r}).write_text('ready'); "
                + ("time.sleep(30)" if times_out else "pass"),
            ],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            text=True,
            start_new_session=True,
        )
        children.append(child)
        deadline = time.monotonic() + 2
        while not ready.exists():
            if time.monotonic() >= deadline:
                raise AssertionError("child did not start")
            time.sleep(0.01)
        return child

    monkeypatch.setitem(
        dataflow.run.__globals__,
        "subprocess",
        SimpleNamespace(
            Popen=launch, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired
        ),
    )
    monkeypatch.setitem(dataflow.run.__globals__, "_reap_orphan_nodes", reaped.append)
    try:
        result = dataflow.run(graph, timeout_s=0.1)
        assert result.timed_out is times_out
        assert result.returncode == (-signal.SIGTERM if times_out else 0)
        assert result.stdout == "out\n"
        assert result.stderr == "err\n"
        assert reaped == [tmp_path]
        assert not (tmp_path / "capture-cleanup.json").exists()
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=5)


@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("failure", [KeyboardInterrupt(), RuntimeError("capture failed")])
def test_capture_exception_reaps_owned_child_and_preserves_exception(
    tmp_path, monkeypatch, dataflow, failure, close_fails
):
    """CON-9/CON-12: cancellation cannot contaminate later graph checks with a live child."""
    graph = tmp_path / "dataflow.yaml"
    graph.write_text("fixture graph")
    retained = tmp_path / "retained.txt"
    children, reaped = [], []

    def launch(*args, **kwargs):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; from pathlib import Path; "
                f"Path({str(retained)!r}).write_text('retained'); time.sleep(30)",
            ],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            text=True,
            start_new_session=True,
        )
        children.append((child, child.communicate))

        def interrupted_communicate(timeout):
            deadline = time.monotonic() + 2
            while not retained.exists():
                if time.monotonic() >= deadline:
                    raise AssertionError("child did not reach the capture boundary")
                time.sleep(0.01)
            raise failure

        if close_fails:
            original_close = child.stdout.close

            def failing_close():
                original_close()
                raise OSError("fixture stream close failed")

            child.stdout.close = failing_close
        child.communicate = interrupted_communicate
        return child

    # Replace only this helper's module binding, preserving the process
    # observation utilities' real subprocess implementation.
    monkeypatch.setitem(
        dataflow.run.__globals__,
        "subprocess",
        SimpleNamespace(
            Popen=launch, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired
        ),
    )
    monkeypatch.setitem(dataflow.run.__globals__, "_reap_orphan_nodes", reaped.append)
    try:
        with pytest.raises(type(failure)) as caught:
            dataflow.run(graph, timeout_s=5)
        assert caught.value is failure
        if close_fails:
            assert any("fixture stream close failed" in note for note in failure.__notes__)
        child, _ = children[0]
        assert child.poll() is not None, "capture exception left its child running"
        assert retained.read_text() == "retained"
        assert reaped == [tmp_path]
        cleanup = json.loads((tmp_path / "capture-cleanup.json").read_text())
        assert cleanup["ok"], cleanup
        assert cleanup["exhaustive"] is False
    finally:
        for child, communicate in children:
            child.communicate = communicate
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            if not child.stdout.closed or not child.stderr.closed:
                communicate(timeout=5)
            else:
                child.wait(timeout=5)
