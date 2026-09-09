"""MON-12/MON-13: fixed local MCP side effects for frontend dispatch tests.

This deliberately performs each accepted call; the external dispatch authority
must prevent a refused call from reaching it. It is not a campaign tool server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
MAX_REQUEST_BYTES = 65536


def serve(output: Path) -> None:
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
    serve(parser.parse_args().output)
