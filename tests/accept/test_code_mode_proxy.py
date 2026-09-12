"""MON-8/MON-12/MON-13: the production RPC boundary reserves before socket delivery."""

import asyncio
import copy
import hashlib
import json

import grpc
import pytest

from aisle.harness._code_mode_protocol import message
from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.accept


def test_nested_proxy_relays_headers_and_blocks_second_callback(tmp_path):
    """MON-8: subscription headers unblock execution; the second callback never reaches client."""
    from aisle.harness.code_mode_authority import CodeModeAuthority
    from aisle.harness.code_mode_proxy import CodeModeProxy

    async def exercise():
        ready = asyncio.Event()
        stop = asyncio.Event()
        completions = []
        path = "/codex.code_mode.v1.CodeModeHost/"

        async def open_session(raw, context):
            yield message("SessionEvent", opened={"session_id": "host"}).SerializeToString()
            await stop.wait()

        async def subscribe(raw, context):
            await context.send_initial_metadata((("host-ready", "true"),))
            await ready.wait()
            for n in (1, 2):
                yield message(
                    "ToolCall",
                    session_id="host",
                    execution_id="exec",
                    cell_id="cell",
                    invocation_id=f"inv-{n}",
                    runtime_tool_call_id=f"tool-{n}",
                    tool_name={"name": "exec_command"},
                    tool_kind=1,
                    input_json=b'{"cmd":"true"}',
                    sequence=n,
                ).SerializeToString()

        async def execute(raw, context):
            ready.set()
            yield message(
                "ExecuteEvent", started={"execution_id": "exec", "cell_id": "cell"}
            ).SerializeToString()
            yield message(
                "ExecuteEvent", outcome={"cell_id": "cell", "completed": {}}
            ).SerializeToString()

        async def complete(raw, context):
            completions.append(message("CompleteToolCallRequest", raw))
            return b""

        backend = grpc.aio.server()
        backend.add_generic_rpc_handlers(
            [
                grpc.method_handlers_generic_handler(
                    "codex.code_mode.v1.CodeModeHost",
                    {
                        "OpenSession": grpc.unary_stream_rpc_method_handler(open_session),
                        "SubscribeToToolCalls": grpc.unary_stream_rpc_method_handler(subscribe),
                        "Execute": grpc.unary_stream_rpc_method_handler(execute),
                        "CompleteToolCall": grpc.unary_unary_rpc_method_handler(complete),
                    },
                )
            ]
        )
        port = backend.add_insecure_port("127.0.0.1:0")
        await backend.start()
        try:
            with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
                authority = CodeModeAuthority(budget, delegated_tools=set())
                async with CodeModeProxy(
                    f"http://127.0.0.1:{port}", authority, output=tmp_path / "rpc"
                ) as proxy:
                    async with grpc.aio.insecure_channel(proxy.address) as client:
                        lease = client.unary_stream(path + "OpenSession")(b"")
                        await lease.read()
                        subscription = client.unary_stream(path + "SubscribeToToolCalls")(
                            message(
                                "SubscribeToToolCallsRequest", session_id="host"
                            ).SerializeToString()
                        )
                        headers = await asyncio.wait_for(subscription.initial_metadata(), 2)
                        assert ("host-ready", "true") in tuple(headers)
                        execution = client.unary_stream(path + "Execute")(
                            message(
                                "ExecuteRequest",
                                session_id="host",
                                execution_id="exec",
                                tool_call_id="outer",
                                source="await tools.exec_command({cmd:'true'})",
                                enabled_tools=[
                                    {
                                        "name": "exec_command",
                                        "tool_name": {"name": "exec_command"},
                                        "kind": 1,
                                    }
                                ],
                            ).SerializeToString()
                        )
                        assert len([raw async for raw in execution]) == 2
                        received = [message("ToolCall", raw) async for raw in subscription]
                        assert [call.invocation_id for call in received] == ["inv-1"]
                        assert len(completions) == 1
                        assert completions[0].invocation_id == "inv-2"
                        assert completions[0].WhichOneof("outcome") == "failed"
                        lease.cancel()
                reference = proxy.reference()
                assert reference["failure"] is None
                assert reference["artifacts"]
            assert budget.reference()["reserved"] == 1
            rows = [
                json.loads(p.read_bytes())
                for p in (tmp_path / "dispatch").glob("*-reservation.json")
            ]
            assert sorted(row["decision"] for row in rows) == ["authorized", "refused"]
            from aisle.harness.code_mode_audit import verify_code_mode_sources

            audit = verify_code_mode_sources(
                {p.name: p.read_bytes() for p in (tmp_path / "rpc").iterdir()},
                expected=reference,
                delegated_tools=set(),
                byte_limit=1024 * 1024,
                dispatch={
                    "artifacts": {
                        p.name: p.read_bytes() for p in (tmp_path / "dispatch").iterdir()
                    },
                    "expected": budget.reference(),
                    "byte_limit": 1024 * 1024,
                },
            )
            assert audit["ok"], audit
            assert audit["native_attempts"] == [1, 2]
            # Reuse this capture: even with self-consistent file hashes, an
            # alleged delivery of the refused callback must fail semantic replay.
            altered = {p.name: p.read_bytes() for p in (tmp_path / "rpc").iterdir()}
            for name, data in altered.items():
                row = json.loads(data)
                if row["phase"] == "refused":
                    row.update(phase="forwarded", frame="")
                    altered[name] = json.dumps(row).encode() + b"\n"
                    break
            altered_reference = copy.deepcopy(reference)
            altered_reference["artifacts"] = {
                name: hashlib.sha256(data).hexdigest() for name, data in altered.items()
            }
            altered_reference["bytes"] = sum(map(len, altered.values()))
            tampered = verify_code_mode_sources(
                altered,
                expected=altered_reference,
                delegated_tools=set(),
                byte_limit=1024 * 1024,
                dispatch={
                    "artifacts": {
                        p.name: p.read_bytes() for p in (tmp_path / "dispatch").iterdir()
                    },
                    "expected": budget.reference(),
                    "byte_limit": 1024 * 1024,
                },
            )
            assert not tampered["ok"]
            assert "disposition" in "; ".join(tampered["errors"])
        finally:
            stop.set()
            await backend.stop(0)

    asyncio.run(asyncio.wait_for(exercise(), 10))


def test_rpc_shutdown_cancellation_waits_for_handler_evidence(tmp_path):
    """MON-12/MON-13: repeated cancellation cannot close a real RPC handler's pending receipt."""
    import threading

    from aisle.harness.code_mode_authority import CodeModeAuthority
    from aisle.harness.code_mode_proxy import CodeModeProxy

    async def exercise():
        entered, release = threading.Event(), threading.Event()
        stopped = asyncio.Event()

        async def open_session(raw, context):
            yield message("SessionEvent", opened={"session_id": "host"}).SerializeToString()
            await stopped.wait()

        backend = grpc.aio.server()
        backend.add_generic_rpc_handlers(
            [
                grpc.method_handlers_generic_handler(
                    "codex.code_mode.v1.CodeModeHost",
                    {"OpenSession": grpc.unary_stream_rpc_method_handler(open_session)},
                )
            ]
        )
        port = backend.add_insecure_port("127.0.0.1:0")
        await backend.start()
        close = None
        proxy = None
        try:
            with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
                proxy = await CodeModeProxy(
                    f"http://127.0.0.1:{port}",
                    CodeModeAuthority(budget, delegated_tools=set()),
                    output=tmp_path / "rpc",
                ).__aenter__()
                retain = proxy._retain

                def held_receipt(rpc, method, phase, raw=b""):
                    if phase == "cancelled":
                        entered.set()
                        assert release.wait(3), "test did not release cancellation receipt"
                    retain(rpc, method, phase, raw)

                proxy._retain = held_receipt
                async with grpc.aio.insecure_channel(proxy.address) as client:
                    lease = client.unary_stream("/codex.code_mode.v1.CodeModeHost/OpenSession")(b"")
                    assert message("SessionEvent", await lease.read()).opened.session_id == "host"
                    close = asyncio.create_task(proxy.__aexit__(None, None, None))
                    assert await asyncio.to_thread(entered.wait, 2)
                    close.cancel()
                    await asyncio.sleep(0)
                    close.cancel()
                    await asyncio.sleep(0)
                    assert not close.done() and not proxy._closed and proxy._fd is not None
                    with pytest.raises(ValueError, match="must close"):
                        proxy.reference()
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(close, 2)
                    lease.cancel()
                assert not proxy._handlers
                reference = proxy.reference()
                rows = {p.name: p.read_bytes() for p in (tmp_path / "rpc").iterdir()}
                assert reference["artifacts"] == {
                    name: hashlib.sha256(raw).hexdigest() for name, raw in rows.items()
                }
                assert sum(json.loads(raw)["phase"] == "cancelled" for raw in rows.values()) == 1
            assert budget.reference()["attempts"] == budget.reference()["reserved"] == 0
        finally:
            release.set()
            stopped.set()
            if close is not None:
                await asyncio.gather(close, return_exceptions=True)
            elif proxy is not None:
                await proxy.__aexit__(None, None, None)
            await backend.stop(0)

    asyncio.run(asyncio.wait_for(exercise(), 10))
