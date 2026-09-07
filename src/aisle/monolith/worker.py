"""Internal controller worker loop; the supervisor must supply OS confinement.

Only module construction and callbacks execute here. Robot primitive objects,
Dora connections and evidence sinks belong to the supervising process.
"""

from __future__ import annotations

import hashlib
from contextlib import redirect_stdout

from aisle.monolith.confinement import ConfinementViolation, guarded, load_module
from aisle.monolith.proxy import PrimitiveClient, PrimitiveProtocolError
from aisle.monolith.wire import WireError, receive, send


def serve(incoming, outgoing, log):
    """Serve sequential Arrow commands on dedicated pipes, returning a process exit code.

    This internal protocol is not the public harness CLI. Its supervisor owns
    deadlines, process cleanup, identity admission and validation of all actions.
    """
    controller = None
    initialized = False
    next_command = 1
    next_request = 1
    command_id = None

    def exchange(request):
        nonlocal next_request
        request_id = next_request
        next_request += 1
        send(outgoing, {"kind": "primitive", "id": request_id, "request": request})
        reply = receive(incoming)
        if (
            type(reply) is not dict
            or set(reply) != {"kind", "id", "reply"}
            or reply["kind"] != "primitive_reply"
            or type(reply["id"]) is not int
            or reply["id"] != request_id
        ):
            raise PrimitiveProtocolError("worker primitive reply sequence differs")
        return reply["reply"]

    client = PrimitiveClient(exchange)
    try:
        while True:
            command_id = None
            command = receive(incoming)
            if type(command) is not dict or type(command.get("id")) is not int:
                raise PrimitiveProtocolError("invalid worker command")
            command_id = command["id"]
            if command_id != next_command:
                raise PrimitiveProtocolError("worker command sequence differs")
            next_command += 1
            operation = command.get("op")
            if not initialized:
                if (
                    set(command) != {"id", "op", "source", "filename"}
                    or operation != "init"
                    or type(command["source"]) is not str
                    or type(command["filename"]) is not str
                ):
                    raise PrimitiveProtocolError("worker requires an initial source command")
                with redirect_stdout(log), guarded():
                    namespace = load_module(command["filename"], command["source"])
                    version = client.root.api_version
                    if namespace.get("API_VERSION") != version:
                        raise RuntimeError("controller primitive API version differs")
                    constructor = namespace.get("Controller")
                    if constructor is None:
                        raise RuntimeError("module defines no Controller class")
                    controller = constructor(client.root, lambda message: print(message, file=log))
                initialized = True
                result = {
                    "api_version": version,
                    "source_sha256": hashlib.sha256(command["source"].encode("utf-8")).hexdigest(),
                }
            elif operation == "event" and set(command) == {"id", "op", "event"}:
                if type(command["event"]) is not dict:
                    raise PrimitiveProtocolError("worker event must be an object")
                with redirect_stdout(log), guarded():
                    result = controller.on_event(command["event"])
            elif operation == "close" and set(command) == {"id", "op"}:
                send(outgoing, {"kind": "result", "id": command_id, "value": None})
                return 0
            else:
                raise PrimitiveProtocolError("invalid worker lifecycle command")
            send(outgoing, {"kind": "result", "id": command_id, "value": result})
    except BaseException as exc:
        category = "module"
        if isinstance(exc, (PrimitiveProtocolError, WireError)):
            category = "protocol"
        elif isinstance(exc, ConfinementViolation):
            category = "confinement"
        try:
            send(
                outgoing,
                {
                    "kind": "error",
                    "id": command_id,
                    "error": {
                        "category": category,
                        "kind": type(exc).__name__,
                        "message": str(exc)[:4096],
                    },
                },
            )
        except (OSError, WireError):
            pass  # A closed or corrupt pipe is still a failed worker process.
        return 1
