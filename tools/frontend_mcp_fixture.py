"""MON-12/MON-13: fixed local MCP side effects for frontend dispatch tests.

This deliberately performs each accepted call; the external dispatch authority
must prevent a refused call from reaching it. It is not a campaign tool server.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
MAX_REQUEST_BYTES = 65536


def serve(output: Path, *, probe_root=None, probe_target=None, probe_marker=None) -> None:
    probe = None
    if any(value is not None for value in (probe_root, probe_target, probe_marker)):
        if (
            not isinstance(probe_root, Path)
            or type(probe_target) is not str
            or not probe_target
            or Path(probe_target).is_absolute()
            or str(Path(probe_target)) != probe_target
            or ".." in Path(probe_target).parts
            or type(probe_marker) is not str
            or not probe_marker.startswith("# AISLE probe ")
            or len(probe_marker) > 256
            or any(ord(c) < 32 for c in probe_marker)
        ):
            raise ValueError("invalid bound MCP effect probe")
        probe = probe_root.absolute() / probe_target
        if probe.resolve() != probe:
            raise ValueError("redirected MCP effect target")
    calls = 0
    while raw := sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1):
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            raise ValueError("MCP fixture request exceeds its frame limit")
        request = json.loads(raw)
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise ValueError("invalid MCP fixture request")
        with (output / "mcp-requests.jsonl").open("a") as stream:
            stream.write(json.dumps(request, allow_nan=False) + "\n")
        if "id" not in request:
            continue
        method = request.get("method")
        response = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aisle-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "record",
                        "description": "Write the local engineering fixture marker",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    }
                ]
            }
            if probe is not None:
                result["tools"].append(
                    {
                        "name": "append",
                        "description": "Append the bound engineering probe marker",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string", "const": probe_target},
                                "marker": {"type": "string", "const": probe_marker},
                            },
                            "required": ["target", "marker"],
                            "additionalProperties": False,
                        },
                    }
                )
        elif (
            probe is not None
            and method == "tools/call"
            and request.get("params", {}).get("name") == "append"
            and request["params"].get("arguments")
            == {"target": probe_target, "marker": probe_marker}
        ):
            if probe.resolve() != probe:
                raise ValueError("redirected MCP effect target")
            fd = os.open(probe, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "ab") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("MCP effect target is not a regular file")
                stream.write(probe_marker.encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            result = {
                "content": [{"type": "text", "text": "fixture marker appended"}],
                "isError": False,
            }
        elif (
            method == "tools/call"
            and request.get("params", {}).get("name") == "record"
            and request["params"].get("arguments", {}) == {}
        ):
            calls += 1
            with (output / "workspace" / f"mcp-marker-{calls}.txt").open("x") as stream:
                stream.write("fixture")
            result = {"content": [{"type": "text", "text": "fixture recorded"}], "isError": False}
        else:
            response["error"] = {"code": -32601, "message": "unsupported fixture method or tool"}
            print(json.dumps(response, allow_nan=False), flush=True)
            continue
        response["result"] = result
        print(json.dumps(response, allow_nan=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-root", type=Path)
    parser.add_argument("--probe-target")
    parser.add_argument("--probe-marker")
    args = parser.parse_args()
    serve(
        args.output,
        probe_root=args.probe_root,
        probe_target=args.probe_target,
        probe_marker=args.probe_marker,
    )
