"""Controller-owned Codex app-server stdio transport for dynamic harness tools.

Only messages read from the owned process pipe reach the dispatch callback.
The caller binds argv, environment, thread configuration, frontend revision and
outer confinement to its admitted plan. This does not cover built-in tools or
attest external confinement; unsupported server requests fail the session.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import signal
import time
from pathlib import Path

MAX_MESSAGE_BYTES = 65536


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate app-server field")
        result[key] = value
    return result


def parse_dynamic_call(message, *, thread_id, turn_id):
    """Validate one server request after acquisition from the owned stdio pipe."""
    if (
        type(message) is not dict
        or set(message) != {"id", "method", "params"}
        or message["method"] != "item/tool/call"
        or type(message["id"]) not in (str, int)
    ):
        raise ValueError("invalid app-server tool request")
    params = message["params"]
    if (
        type(params) is not dict
        or set(params) != {"threadId", "turnId", "callId", "namespace", "tool", "arguments"}
        or params["threadId"] != thread_id
        or params["turnId"] != turn_id
        or params["namespace"] != "harness"
        or params["tool"] not in ("check", "run")
        or type(params["arguments"]) is not dict
        or params["arguments"]
        or any(
            type(params[key]) is not str or not 0 < len(params[key]) <= 256
            for key in ("threadId", "turnId", "callId")
        )
    ):
        raise ValueError("unbound or unsupported app-server tool call")
    return {
        "turn_id": turn_id,
        "call_id": params["callId"],
        "tool_name": "harness." + params["tool"],
    }


class AppServerUsage:
    """Meter cumulative new-input plus output tokens using the existing Codex budget unit."""

    def __init__(self, thread_id, turn_id):
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.total = None

    def feed(self, params):
        if (
            type(params) is not dict
            or params.get("threadId") != self.thread_id
            or params.get("turnId") != self.turn_id
        ):
            raise ValueError("usage belongs to another thread or turn")
        usage = params.get("tokenUsage")
        if type(usage) is not dict or not {"total", "last"}.issubset(usage):
            raise ValueError("missing app-server usage")
        fields = {
            "inputTokens",
            "cachedInputTokens",
            "outputTokens",
            "reasoningOutputTokens",
            "totalTokens",
        }
        for name in ("total", "last"):
            value = usage[name]
            if (
                type(value) is not dict
                or not fields.issubset(value)
                or set(value) - fields - {"cacheWriteInputTokens"}
                or any(type(v) is not int or v < 0 for v in value.values())
                or value["cachedInputTokens"] > value["inputTokens"]
                or value["reasoningOutputTokens"] > value["outputTokens"]
                or value["totalTokens"] != value["inputTokens"] + value["outputTokens"]
            ):
                raise ValueError("invalid app-server usage breakdown")
        total = usage["total"]
        if any(usage["last"][key] > total[key] for key in fields):
            raise ValueError("last usage exceeds cumulative usage")
        if self.total is not None and any(total[key] < self.total[key] for key in fields):
            raise ValueError("app-server cumulative usage moved backwards")
        spend = total["inputTokens"] - total["cachedInputTokens"] + total["outputTokens"]
        if self.total is not None and spend < self.report()["tokens"]:
            raise ValueError("app-server usage reduced previously observed spend")
        self.total = dict(total)

    def report(self):
        if self.total is None:
            raise ValueError("app-server usage is missing")
        return {
            "tokens": self.total["inputTokens"]
            - self.total["cachedInputTokens"]
            + self.total["outputTokens"],
            "tokens_generated": self.total["outputTokens"],
        }


def dynamic_tools(operations=("check", "run")):
    """Declare the fixed check/run API without removing other frontend tools."""
    return [
        {
            "type": "namespace",
            "name": "harness",
            "description": "AISLE harness tools",
            "tools": [
                {
                    "type": "function",
                    "name": operation,
                    "description": f"Request the admitted harness {operation}",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                }
                for operation in operations
            ],
        }
    ]


def run_app_server(
    argv,
    *,
    cwd,
    env,
    output,
    thread_params,
    input_items,
    handle_call,
    timeout_s,
    token_ceiling=None,
    on_message=None,
):
    """Run one fresh thread/turn over a private pipe and retain protocol bytes.

    ``handle_call(call, raw_message)`` may return an awaitable. It must return the
    dynamic tool result only after controller authorization/execution. Callbacks
    doing blocking work must enforce their own finite deadline. The write-time
    reference returned here must be retained by the trusted controller.
    """
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 86400:
        raise ValueError("invalid app-server deadline")
    if token_ceiling is not None and (type(token_ceiling) is not int or token_ceiling <= 0):
        raise ValueError("invalid token ceiling")
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError("redirected app-server output")
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    started = time.monotonic()

    def retain(name, raw):
        with (output / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        hashes[name] = hashlib.sha256(raw).hexdigest()

    def encode(value):
        return json.dumps(value, allow_nan=False).encode() + b"\n"

    retain(
        "invocation.json",
        encode(
            {
                "argv": argv,
                "cwd": str(cwd),
                "environment_sha256": hashlib.sha256(
                    json.dumps(env, sort_keys=True).encode()
                ).hexdigest(),
                "thread_params": thread_params,
                "input_items": input_items,
                "timeout_s": timeout_s,
                "transport": "owned_stdio",
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ),
    )

    async def run():
        with (output / "stderr.log").open("xb") as stderr:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
                limit=MAX_MESSAGE_BYTES,
            )
            incoming = outgoing = 0
            calls = set()
            request_ids = set()
            thread_id = turn_id = None
            pending = []
            completed = None

            async def send(value):
                nonlocal outgoing
                data = encode(value)
                if len(data) > MAX_MESSAGE_BYTES:
                    raise ValueError("app-server outgoing message exceeds limit")
                outgoing += 1
                retain(f"{outgoing:08d}-sent.json", data)
                process.stdin.write(data)
                await process.stdin.drain()

            async def receive():
                nonlocal incoming
                raw = await process.stdout.readline()
                if not raw or not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE_BYTES:
                    raise ValueError("app-server ended or exceeded its message limit")
                incoming += 1
                retain(f"{incoming:08d}-received.json", raw)
                if on_message is not None:
                    reason = on_message(raw.decode())
                    if reason is not None:
                        raise ValueError(f"frontend observation stopped session: {reason}")
                value = json.loads(raw, object_pairs_hook=_object)
                if type(value) is not dict:
                    raise ValueError("invalid app-server message")
                if "method" in value:
                    if type(value["method"]) is not str or not value["method"]:
                        raise ValueError("invalid app-server method")
                elif set(value) not in ({"id", "result"}, {"id", "error"}):
                    raise ValueError("invalid app-server response fields")
                if "id" in value and type(value["id"]) not in (str, int):
                    raise ValueError("invalid app-server message identity")
                return value, raw

            async def reply_for(identity):
                while True:
                    value, _ = await receive()
                    if "method" in value:
                        if "id" in value:
                            raise ValueError("unexpected server request before active turn")
                        pending.append(value)
                        continue
                    if value.get("id") != identity or "error" in value or "result" not in value:
                        raise ValueError("app-server setup failed or response identity differs")
                    return value["result"]

            try:
                async with asyncio.timeout(timeout_s):
                    await send(
                        {
                            "id": "initialize",
                            "method": "initialize",
                            "params": {
                                "clientInfo": {"name": "aisle", "version": "1"},
                                "capabilities": {"experimentalApi": True},
                            },
                        }
                    )
                    await reply_for("initialize")
                    await send({"method": "initialized"})
                    await send({"id": "thread", "method": "thread/start", "params": thread_params})
                    thread_id = (await reply_for("thread"))["thread"]["id"]
                    await send(
                        {
                            "id": "turn",
                            "method": "turn/start",
                            "params": {"threadId": thread_id, "input": input_items},
                        }
                    )
                    turn_id = (await reply_for("turn"))["turn"]["id"]
                    meter = AppServerUsage(thread_id, turn_id)

                    def usage(value):
                        if value.get("method") == "thread/tokenUsage/updated":
                            meter.feed(value["params"])
                            if (
                                token_ceiling is not None
                                and meter.report()["tokens"] >= token_ceiling
                            ):
                                raise ValueError("app-server token budget exhausted")

                    for value in pending:
                        usage(value)
                    while True:
                        value, raw = await receive()
                        usage(value)
                        if value.get("method") == "item/tool/call":
                            call = parse_dynamic_call(value, thread_id=thread_id, turn_id=turn_id)
                            identity = (call["turn_id"], call["call_id"])
                            wire_id = (type(value["id"]), value["id"])
                            if identity in calls or wire_id in request_ids:
                                raise ValueError("app-server request identity replay")
                            calls.add(identity)
                            request_ids.add(wire_id)
                            result = handle_call(call, raw)
                            if inspect.isawaitable(result):
                                result = await result
                            await send({"id": value["id"], "result": result})
                        elif "id" in value:
                            raise ValueError("unsupported app-server request or response")
                        elif value.get("method") == "turn/completed":
                            params = value["params"]
                            if (
                                params["threadId"] != thread_id
                                or params["turn"]["id"] != turn_id
                                or params["turn"]["status"] != "completed"
                            ):
                                raise ValueError("app-server turn failed or identity differs")
                            completed = {
                                "thread_id": thread_id,
                                "turn_id": turn_id,
                                "dynamic_calls": len(calls),
                                **(
                                    meter.report()
                                    if token_ceiling is not None or meter.total is not None
                                    else {}
                                ),
                            }
                            return completed
            finally:
                if completed is not None:
                    process.stdin.close()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1)
                    except TimeoutError:
                        pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                if completed is not None:
                    completed["rc"] = process.returncode
                    if process.returncode != 0:
                        raise ValueError("app-server did not exit cleanly after turn completion")

    try:
        result = asyncio.run(run())
    except BaseException as exc:
        try:
            retain("failure.json", encode({"error_type": type(exc).__name__, "error": str(exc)}))
        except BaseException as retention_error:
            exc.add_note(f"failure evidence retention failed: {retention_error}")
        raise
    return {
        **result,
        "artifacts": dict(hashes),
        "complete_coverage": False,
        "confinement_verified": False,
        "wall_s": time.monotonic() - started,
        "stopped": "agent_done",
        "stream_complete": True,
    }
