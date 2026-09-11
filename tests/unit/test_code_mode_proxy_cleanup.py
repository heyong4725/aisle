"""MON-12/MON-13: cancellation cannot outlive owned evidence writes."""

import asyncio
import threading

import pytest

pytestmark = pytest.mark.unit


def test_repeated_cancellation_waits_for_pending_evidence_write(tmp_path):
    """MON-12: repeated shutdown signals cannot release evidence ownership mid-write."""
    from aisle.harness.code_mode_proxy import CodeModeProxy

    async def exercise():
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        proxy = CodeModeProxy("http://127.0.0.1:12345", None, output=tmp_path / "rpc")

        def write():
            started.set()
            release.wait(2)
            completed.set()

        task = asyncio.create_task(proxy._thread(write))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not task.done(), "shutdown escaped while the evidence writer was still active"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert completed.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["server", "handler", "channel"])
def test_repeated_cancellation_cannot_close_proxy_before_owned_cleanup(tmp_path, stage):
    """MON-12/MON-13: a closed RPC reference requires every owned cleanup stage to finish."""
    import os

    from aisle.harness.code_mode_proxy import CodeModeProxy

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        completed = []
        proxy = CodeModeProxy("http://127.0.0.1:12345", None, output=tmp_path / "rpc")
        proxy._fd = os.open(tmp_path, os.O_RDONLY)

        async def finish(name):
            if name == stage:
                entered.set()
                await release.wait()
            completed.append(name)

        class Server:
            async def stop(self, grace):
                await finish("server")

        class Channel:
            async def close(self):
                await finish("channel")

        proxy._server, proxy._channel = Server(), Channel()
        handler = asyncio.create_task(finish("handler"))
        proxy._handlers.add(handler)
        task = asyncio.create_task(proxy.__aexit__(None, None, None))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "proxy cleanup escaped while owned work remained"
            assert proxy._fd is not None and not proxy._closed
            with pytest.raises(ValueError, match="must close"):
                proxy.reference()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert set(completed) == {"server", "handler", "channel"}
            assert proxy._fd is None and proxy._closed
        finally:
            release.set()
            await asyncio.gather(task, handler, return_exceptions=True)
            if proxy._fd is not None:
                os.close(proxy._fd)

    asyncio.run(exercise())
