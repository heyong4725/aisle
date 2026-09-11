"""MON-13: the controller owns the pinned host and both process lifetimes."""

import asyncio
import hashlib
import sys

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failure", ["digest", "host_exit"])
def test_host_failure_never_launches_frontend(tmp_path, failure):
    """MON-13: drift or startup failure cannot fall back to an unmediated frontend."""
    from aisle.harness.code_mode_runner import run_code_mode_app_server

    marker = tmp_path / "host-started"
    host = tmp_path / "host"
    host.write_text(f"#!{sys.executable}\nimport pathlib\npathlib.Path({str(marker)!r}).touch()\n")
    host.chmod(0o700)
    expected = hashlib.sha256(host.read_bytes()).hexdigest() if failure == "host_exit" else "0" * 64
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
        with pytest.raises(ValueError, match="host"):
            asyncio.run(
                run_code_mode_app_server(
                    host={"path": str(host), "sha256": expected},
                    dispatch=budget,
                    delegated_tools=set(),
                    output=tmp_path / "code-mode",
                    references={},
                    argv=["must-not-launch"],
                    cwd=tmp_path,
                    env={},
                    protocol_output=tmp_path / "frontend",
                    thread_params={},
                    input_items=[],
                    handle_call=lambda *_: pytest.fail("unmediated frontend call"),
                    timeout_s=2,
                )
            )
    assert marker.exists() == (failure == "host_exit")
    assert not (tmp_path / "frontend").exists()


@pytest.mark.parametrize("frontend_fails", [False, True])
def test_closed_reference_retains_owned_nested_endpoints(tmp_path, monkeypatch, frontend_fails):
    """MON-12/MON-13: success and failure retain the host-to-proxy launch binding after closure."""
    from aisle.harness import code_mode_runner

    host = tmp_path / "host"
    host.write_text(
        f"#!{sys.executable}\nimport time\n"
        "print('http://127.0.0.1:7654', flush=True)\ntime.sleep(30)\n"
    )
    host.chmod(0o700)
    binding = {"path": str(host), "sha256": hashlib.sha256(host.read_bytes()).hexdigest()}
    captured = {}

    class Proxy:
        address = "127.0.0.1:8765"
        backend = "127.0.0.1:7654"

        def __init__(self, endpoint, *args, **kwargs):
            assert endpoint == "http://127.0.0.1:7654"
            self.failed = asyncio.Event()
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            self.closed = True

        def check(self):
            pass

        def reference(self):
            assert self.closed
            return {"closed": True}

    async def frontend(argv, **kwargs):
        captured["argv"] = argv
        if frontend_fails:
            raise ValueError("frontend failed")
        return {"ok": True}

    monkeypatch.setattr(code_mode_runner, "CodeModeProxy", Proxy)
    monkeypatch.setattr(code_mode_runner, "run_app_server_async", frontend)
    references = {}
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
        coroutine = code_mode_runner.run_code_mode_app_server(
            host=binding,
            dispatch=budget,
            delegated_tools=set(),
            output=tmp_path / "code-mode",
            references=references,
            argv=["frontend"],
            cwd=tmp_path,
            env={},
            protocol_output=tmp_path / "protocol",
            timeout_s=2,
        )
        if frontend_fails:
            with pytest.raises(ValueError, match="frontend failed"):
                asyncio.run(coroutine)
        else:
            assert asyncio.run(coroutine) == {"ok": True}
    assert captured["argv"] == [
        "frontend",
        "--code-mode-host",
        "http://127.0.0.1:8765",
        "-c",
        "features.code_mode=true",
    ]
    reference = references["code_mode"]
    assert reference["listener"] == {
        "schema_version": "aisle.code-mode-listener.v1",
        "session_id": "matched",
        "host": "127.0.0.1",
        "port": 8765,
    }
    assert reference["backend"] == "127.0.0.1:7654"
    assert reference["host"] == binding
    assert reference["failure"] == ("ValueError: frontend failed" if frontend_fails else None)


def test_repeated_cancellation_waits_for_nested_host_reaping(tmp_path, monkeypatch):
    """MON-12/MON-13: nested cleanup cannot finish or publish evidence before host reaping."""
    import os
    import signal

    from aisle.harness import code_mode_runner

    host = tmp_path / "host"
    host.write_text(
        f"#!{sys.executable}\nimport time\n"
        "print('http://127.0.0.1:7654', flush=True)\ntime.sleep(30)\n"
    )
    host.chmod(0o700)
    binding = {"path": str(host), "sha256": hashlib.sha256(host.read_bytes()).hexdigest()}

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        real_spawn, real_kill = asyncio.create_subprocess_exec, os.killpg
        process = None
        original_wait = None
        killing = reaped = False
        references = {}

        async def spawn(*args, **kwargs):
            nonlocal process, original_wait
            process = await real_spawn(*args, **kwargs)
            original_wait = process.wait

            async def wait():
                nonlocal reaped
                if killing:
                    entered.set()
                    await release.wait()
                    result = await original_wait()
                    reaped = True
                    return result
                return await original_wait()

            process.wait = wait
            return process

        def kill(*args):
            nonlocal killing
            killing = True
            return real_kill(*args)

        class Proxy:
            address, backend = "127.0.0.1:8765", "127.0.0.1:7654"

            def __init__(self, *args, **kwargs):
                self.failed = asyncio.Event()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            def check(self):
                pass

            def reference(self):
                assert reaped, "reference published before host reaping"
                return {"closed": True}

        async def frontend(*args, **kwargs):
            raise ValueError("frontend failed")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(os, "killpg", kill)
        monkeypatch.setattr(code_mode_runner, "CodeModeProxy", Proxy)
        monkeypatch.setattr(code_mode_runner, "run_app_server_async", frontend)
        with DispatchBudget(tmp_path / "dispatch", session_id="session", ceiling=1) as budget:
            task = asyncio.create_task(
                code_mode_runner.run_code_mode_app_server(
                    host=binding,
                    dispatch=budget,
                    delegated_tools=set(),
                    output=tmp_path / "code-mode",
                    references=references,
                    argv=["frontend"],
                    cwd=tmp_path,
                    env={},
                    protocol_output=tmp_path / "protocol",
                    timeout_s=2,
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "nested cleanup escaped before host reaping"
                assert not references
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                assert reaped and references["code_mode"]["failure"] is not None
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if process is not None:
                    if process.returncode is None:
                        try:
                            real_kill(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await original_wait()

    asyncio.run(exercise())
