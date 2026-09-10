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
