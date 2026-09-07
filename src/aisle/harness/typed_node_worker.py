"""Internal typed policy worker entry point; not a standalone sandbox or public CLI.

The supervisor must supply a bound source/dependency bundle, isolated environment,
OS confinement, request validation, deadlines and retained process evidence.
The Node facade preserves the policy API; it does not enforce OS authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import redirect_stdout
from importlib.machinery import ModuleSpec
from types import ModuleType

from aisle.harness.typed_node_requests import NodeRequestError, RemoteNode
from aisle.monolith.wire import WireError, receive, send

MODULES = frozenset(
    {
        "aisle.nodes.segmented_pose",
        "aisle.nodes.grasp_topdown",
        "aisle.nodes.ik_trajectory",
        "aisle.nodes.task_state_machine",
    }
)


def validate_configuration(configuration):
    """Validate data-only node settings; they confer no additional OS authority."""
    if (
        type(configuration) is not dict
        or set(configuration) != {"environment", "arguments"}
        or type(configuration["environment"]) is not dict
        or type(configuration["arguments"]) is not list
    ):
        raise NodeRequestError("typed worker configuration is invalid")
    environment, arguments = configuration["environment"], configuration["arguments"]
    if (
        len(environment) > 256
        or len(arguments) > 256
        or any(
            type(key) is not str
            or not key
            or "=" in key
            or "\x00" in key
            or type(value) is not str
            or "\x00" in value
            for key, value in environment.items()
        )
        or any(type(value) is not str or "\x00" in value for value in arguments)
        or len(json.dumps(configuration, ensure_ascii=True).encode()) > 65536
    ):
        raise NodeRequestError("typed worker configuration exceeds its data contract")
    return {"environment": dict(environment), "arguments": list(arguments)}


def serve(incoming, outgoing, log):
    """Verify one source declaration and execute it as a Python script in this child."""
    source_hash = None
    module_name = None
    facade = None
    authored = None
    created = False
    previous_main = sys.modules.get("__main__")
    previous_argv = sys.argv
    previous_environment = None

    def exchange(request):
        send(outgoing, {"kind": "node", "request": request})
        return receive(incoming)

    remote = RemoteNode(exchange)
    try:
        declaration = receive(incoming)
        if (
            type(declaration) is not dict
            or set(declaration)
            not in (
                {"op", "module", "source", "source_sha256"},
                {"op", "module", "source", "source_sha256", "configuration"},
            )
            or declaration["op"] != "init"
            or type(declaration["module"]) is not str
            or declaration["module"] not in MODULES
            or type(declaration["source"]) is not str
        ):
            raise NodeRequestError("typed worker source declaration is invalid")
        configuration = validate_configuration(
            declaration.get("configuration", {"environment": {}, "arguments": []})
        )
        source_hash = hashlib.sha256(declaration["source"].encode()).hexdigest()
        if declaration["source_sha256"] != source_hash:
            raise NodeRequestError("typed worker source identity differs")
        module_name = declaration["module"]
        if module_name in sys.modules or "aisle.turn_node" in sys.modules:
            raise NodeRequestError("typed worker bootstrap is not fresh")

        import aisle

        if hasattr(aisle, "turn_node"):
            raise NodeRequestError("typed worker bootstrap exposes an existing turn wrapper")

        def node_factory():
            nonlocal created
            if created:
                raise NodeRequestError("typed worker may create only its assigned Node")
            created = True
            return remote

        facade = ModuleType("aisle.turn_node")
        facade.__spec__ = ModuleSpec("aisle.turn_node", loader=None)
        facade.Node = node_factory
        sys.modules["aisle.turn_node"] = facade
        aisle.turn_node = facade
        filename = "src/" + module_name.replace(".", "/") + ".py"
        authored = ModuleType("__main__")
        authored.__file__ = filename
        authored.__package__ = None
        authored.__spec__ = None
        sys.modules["__main__"] = authored
        sys.argv = [filename, *configuration["arguments"]]
        previous_environment = dict(os.environ)
        os.environ.update(configuration["environment"])
        send(outgoing, {"kind": "ready", "module": module_name, "source_sha256": source_hash})
        with redirect_stdout(log):
            try:
                exec(
                    compile(declaration["source"], filename, "exec", dont_inherit=True),
                    authored.__dict__,
                )
            except SystemExit as exc:
                if exc.code is not None and not (isinstance(exc.code, int) and exc.code == 0):
                    raise
        send(
            outgoing,
            {
                "kind": "finished",
                "module": module_name,
                "source_sha256": source_hash,
                "node_created": created,
                "input_exhausted": remote.exhausted,
            },
        )
        return 0
    except BaseException as exc:
        category = "protocol" if isinstance(exc, (NodeRequestError, WireError)) else "module"
        try:
            send(
                outgoing,
                {
                    "kind": "error",
                    "module": module_name,
                    "source_sha256": source_hash,
                    "error": {
                        "category": category,
                        "type": type(exc).__name__,
                        "message": str(exc)[:4096],
                    },
                },
            )
        except BaseException:
            pass  # The supervisor retains a broken or truncated protocol stream.
        return 1
    finally:
        sys.argv = previous_argv
        if previous_environment is not None:
            os.environ.clear()
            os.environ.update(previous_environment)
        if authored is not None and sys.modules.get("__main__") is authored:
            if previous_main is None:
                del sys.modules["__main__"]
            else:
                sys.modules["__main__"] = previous_main
        if facade is not None:
            if sys.modules.get("aisle.turn_node") is facade:
                del sys.modules["aisle.turn_node"]
            import aisle

            if getattr(aisle, "turn_node", None) is facade:
                del aisle.turn_node
