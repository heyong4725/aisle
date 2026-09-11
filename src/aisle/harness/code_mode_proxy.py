"""Owned Code Mode RPC relay with durable evidence and nested admission.

The launcher owns both endpoints and must enforce external process/network
confinement. Loopback addressing alone does not authenticate a caller. A failure
must terminate the frontend; callers must monitor ``failed`` and call ``check``
after shutdown. This component never claims complete frontend route coverage.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import grpc

from aisle.harness._code_mode_protocol import DESCRIPTOR, message
from aisle.harness.code_mode_authority import CallbackRetired, _decode, _require
from aisle.harness.frontend_dispatch import DispatchRefused

_SERVICE = "codex.code_mode.v1.CodeModeHost"
_LIMIT = 16 * 1024 * 1024


class CodeModeProxy:
    """One bounded relay lifetime; reuse or restart requires a fresh authority."""

    def __init__(self, backend_url, authority, *, output, byte_limit=64 * 1024 * 1024):
        parsed = urlsplit(backend_url)
        _require(
            parsed.scheme in {"http", "grpc"}
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and parsed.port > 0
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment,
            "host must use an owned loopback endpoint",
        )
        _require(type(byte_limit) is int and byte_limit > 0, "invalid RPC evidence limit")
        self.backend = f"127.0.0.1:{parsed.port}"
        self.authority = authority
        self.output = Path(output).absolute()
        self.byte_limit = byte_limit
        self._bytes = 0
        self._hashes = {}
        self._rpc = 0
        self._closed = False
        self._fd = None
        self._channel = None
        self._server = None
        self._failure = None
        self._lock = asyncio.Lock()
        self.failed = asyncio.Event()
        self._handlers = set()

    async def _thread(self, function, *args):
        # Cancellation cannot close a directory descriptor underneath a write
        # still running in the executor, or release a lock before that write ends.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        result = task.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    def _retain(self, rpc, method, phase, raw=b""):
        data = (
            json.dumps(
                {
                    "rpc": rpc,
                    "method": method,
                    "phase": phase,
                    "frame": base64.b64encode(raw).decode(),
                },
                sort_keys=True,
            ).encode()
            + b"\n"
        )
        _require(
            len(self._hashes) < 10000 and self._bytes + len(data) <= self.byte_limit,
            "RPC evidence limit exceeded",
        )
        name = f"{len(self._hashes) + 1:08d}.json"
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self._fd)
        self._bytes += len(data)
        self._hashes[name] = hashlib.sha256(data).hexdigest()

    def check(self):
        if self._failure is not None:
            raise ValueError("Code Mode relay failed: " + self._failure)

    def _fail(self, exc):
        if self._failure is None:
            self._failure = type(exc).__name__ + ": " + str(exc)
        self.failed.set()

    async def __aenter__(self):
        _require(not self._closed and self._fd is None, "RPC relay cannot restart")
        _require(self.output.resolve() == self.output, "redirected RPC evidence path")
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._fd = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            options = [
                ("grpc.max_receive_message_length", _LIMIT),
                ("grpc.max_send_message_length", _LIMIT),
            ]
            self._channel = grpc.aio.insecure_channel(self.backend, options=options)
            await asyncio.wait_for(self._channel.channel_ready(), 5)
            self._server = grpc.aio.server(options=options)
            handlers = {}
            for method in DESCRIPTOR.services_by_name["CodeModeHost"].methods:

                async def handler(raw, context, method=method):
                    task = asyncio.current_task()
                    self._handlers.add(task)
                    try:
                        return await self._relay(method, raw, context)
                    except Exception as exc:
                        self._fail(exc)
                        raise
                    finally:
                        self._handlers.discard(task)

                factory = (
                    grpc.unary_stream_rpc_method_handler
                    if method.server_streaming
                    else grpc.unary_unary_rpc_method_handler
                )
                handlers[method.name] = factory(handler)
            self._server.add_generic_rpc_handlers(
                [grpc.method_handlers_generic_handler(_SERVICE, handlers)]
            )
            port = self._server.add_insecure_port("127.0.0.1:0")
            _require(port > 0, "cannot bind owned RPC endpoint")
            self.address = f"127.0.0.1:{port}"
            await self._server.start()
            return self
        except BaseException as exc:
            self._fail(exc)
            await self.__aexit__(None, None, None)
            raise

    def _request(self, method, raw):
        request = _decode(method.input_type.name, raw)
        if method.name == "Execute":
            self.authority.execute(raw)
        elif method.name == "CompleteToolCall":
            self.authority.complete(raw)
        elif method.name != "OpenSession":
            self.authority._session(request.session_id)
        return request

    async def _relay(self, method, raw, context):
        call = None
        lease = None
        rpc = None
        path = "/" + _SERVICE + "/" + method.name
        try:
            async with self._lock:
                self.check()
                self._rpc += 1
                rpc = self._rpc
                await self._thread(self._retain, rpc, method.name, "request", raw)
                request = await self._thread(self._request, method, raw)
            factory = (
                self._channel.unary_stream if method.server_streaming else self._channel.unary_unary
            )
            call = factory(path)(raw)
            # The subscription must expose headers before Execute can start. Waiting
            # for its first message here deadlocks the genuine App Server client.
            await context.send_initial_metadata(await call.initial_metadata())
            if not method.server_streaming:
                response = await call
                async with self._lock:
                    self.check()
                    await self._thread(self._retain, rpc, method.name, "response", response)
                    _decode(method.output_type.name, response)
                    if method.name == "CloseSession":
                        await self._thread(self.authority.close_session, request.session_id)
                    await self._thread(self._retain, rpc, method.name, "returned")
                return response
            async for response in call:
                async with self._lock:
                    self.check()
                    await self._thread(self._retain, rpc, method.name, "response", response)
                    value = _decode(method.output_type.name, response)
                    if method.name == "OpenSession":
                        if lease is None:
                            _require(
                                value.WhichOneof("event") == "opened", "missing session opening"
                            )
                            lease = value.opened.session_id
                            await self._thread(self.authority.open_session, lease)
                        else:
                            await self._thread(self.authority.session_event, lease, response)
                    elif method.name == "Execute":
                        await self._thread(
                            self.authority.execution_event,
                            request.session_id,
                            request.execution_id,
                            response,
                        )
                    elif method.name == "SubscribeToToolCalls":
                        _require(
                            value.session_id == request.session_id, "subscription session differs"
                        )
                        loop = asyncio.get_running_loop()

                        def deliver(frame, loop=loop):
                            asyncio.run_coroutine_threadsafe(context.write(frame), loop).result()

                        try:
                            await self._thread(self.authority.callback, response, deliver)
                        except CallbackRetired:
                            await self._thread(self._retain, rpc, method.name, "retired")
                            continue
                        except DispatchRefused as exc:
                            if str(exc) != "dispatch budget exhausted":
                                raise
                            refusal = message(
                                "CompleteToolCallRequest",
                                session_id=value.session_id,
                                invocation_id=value.invocation_id,
                                failed={"message": "AISLE nested dispatch budget exhausted"},
                            ).SerializeToString()
                            await self._thread(self._retain, rpc, method.name, "refused", refusal)
                            await self._channel.unary_unary("/" + _SERVICE + "/CompleteToolCall")(
                                refusal
                            )
                            continue
                        await self._thread(self._retain, rpc, method.name, "forwarded")
                        continue
                    await context.write(response)
                    await self._thread(self._retain, rpc, method.name, "forwarded")
            async with self._lock:
                if method.name == "Execute":
                    await self._thread(
                        self.authority.execution_finished, request.session_id, request.execution_id
                    )
                if lease is not None:
                    await self._thread(self.authority.close_session, lease)
                await self._thread(self._retain, rpc, method.name, "finished")
        except asyncio.CancelledError:
            if rpc is not None and self._fd is not None:
                async with self._lock:
                    if lease is not None:
                        await self._thread(self.authority.close_session, lease)
                    await self._thread(self._retain, rpc, method.name, "cancelled")
            raise
        except BaseException as exc:
            self._fail(exc)
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "AISLE Code Mode relay failed")
        finally:
            if call is not None:
                call.cancel()
            if lease is not None:
                async with self._lock:
                    if self.authority._sessions.get(lease):
                        await self._thread(self.authority.close_session, lease)

    async def __aexit__(self, *_):
        async def close():
            try:
                if self._server is not None:
                    await self._server.stop(0)
                if self._handlers:
                    await asyncio.gather(*tuple(self._handlers), return_exceptions=True)
                if self._channel is not None:
                    await self._channel.close()
            finally:
                if self._fd is not None:
                    os.close(self._fd)
                    self._fd = None
                self._closed = True

        cleanup = asyncio.create_task(close())
        cancellation = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                cancellation = exc
        cleanup.result()
        if cancellation is not None:
            raise cancellation

    def reference(self):
        _require(self._closed and self._fd is None, "RPC relay must close before audit")
        return {
            "artifacts": dict(self._hashes),
            "bytes": self._bytes,
            "failure": self._failure,
            "complete_coverage": False,
            "confinement_verified": False,
        }
