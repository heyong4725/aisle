"""MON-12/MON-13: measure actual Claude Bash hooks using a scripted local provider.

This fixture preserves the built-in catalog and measures one Bash route. It
neither enforces a campaign budget nor attests complete coverage or confinement.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import frontend_codex_probe as common

COMMAND = "printf fixture > marker.txt"
CALL_ID = "toolu_fixture"
INPUT = {"command": COMMAND, "description": "Local fixture marker"}
MODES = ("baseline", "deny", "timeout", "exit1", "malformed", "missing_command")


def summarize_probe(evidence):
    """Require matched process, provider result, hook and side-effect evidence."""
    result = {
        "ok": False,
        "tool_executed": None,
        "tool_blocked": None,
        "complete_coverage": False,
        "confinement_verified": False,
        "errors": [],
    }
    errors = result["errors"]
    requests = evidence["requests"]
    if evidence["rc"] != 0 or evidence["timed_out"]:
        errors.append("frontend did not finish normally")
    if len(requests) != 3 or any(
        not row["fixture_key"] or row["authorization_present"] for row in requests
    ):
        errors.append("fixture requires exactly three requests with only the dummy key")
    elif requests[0]["tools"] or any(
        not any(t.get("name") == "Bash" for t in row["tools"]) for row in requests[1:]
    ):
        errors.append("fixture preliminary request or Bash catalog differs")
    replies = [item for row in requests for item in row["results"]]
    if len(replies) != 1 or replies[0].get("tool_use_id") != CALL_ID:
        errors.append("tool result identity or cardinality differs")
    hook = evidence["hook"]
    if hook is not None and any(
        hook.get(k) != v
        for k, v in {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": CALL_ID,
            "tool_input": INPUT,
        }.items()
    ):
        errors.append("hook identity or command differs")
    if not errors:
        reply = replies[0]
        blocked = reply.get("is_error") is True and reply.get("content") == "AISLE fixture denial"
        if blocked:
            if hook is None or evidence["marker"] is not None:
                errors.append("denial lacks matching hook or still produced a side effect")
        elif reply.get("is_error") is not False or evidence["marker"] != "fixture":
            errors.append("neither explicit denial nor successful side effect was observed")
        if not errors:
            result.update(ok=True, tool_executed=not blocked, tool_blocked=blocked)
    return result


def select_tool_call(body, *, issued):
    """Do not spend the fixture instruction on Claude's tool-less preliminary call."""
    return not issued and any(tool.get("name") == "Bash" for tool in body.get("tools", []))


def _events(first, model, index):
    block = (
        {"type": "tool_use", "id": CALL_ID, "name": "Bash", "input": {}}
        if first
        else {"type": "text", "text": ""}
    )
    delta = (
        {"type": "input_json_delta", "partial_json": json.dumps(INPUT)}
        if first
        else {"type": "text_delta", "text": "fixture done"}
    )
    events = [
        {
            "type": "message_start",
            "message": {
                "id": f"msg_fixture_{index}",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if first else "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _server(requests, errors, output):
    issued = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal issued
            try:
                self.connection.settimeout(5)
                length = int(self.headers.get("Content-Length", "-1"))
                if (
                    self.path != "/v1/messages?beta=true"
                    or not 0 <= length <= common.LIMIT
                    or len(requests) >= 3
                ):
                    raise ValueError("unexpected request path, size or count")
                if self.headers.get("Content-Encoding") is not None:
                    raise ValueError("unsupported fixture request encoding")
                body = json.loads(self.rfile.read(length))
                selected = {
                    "path": self.path,
                    "model": body.get("model"),
                    "tools": body.get("tools", []),
                    "fixture_key": self.headers.get("x-api-key") == "aisle-fixture-key",
                    "authorization_present": "Authorization" in self.headers,
                    "results": [
                        block
                        for msg in body.get("messages", [])
                        for block in (
                            msg.get("content", []) if isinstance(msg.get("content"), list) else []
                        )
                        if block.get("type") == "tool_result"
                    ],
                }
                requests.append(selected)
                common._write(output / f"request-{len(requests)}.json", selected)
                if not selected["fixture_key"] or selected["authorization_present"]:
                    raise ValueError("fixture refuses credentials other than its dummy key")
                first = select_tool_call(body, issued=issued)
                issued = issued or first
                data = _events(first, body["model"], len(requests))
                (output / f"response-{len(requests)}.sse").write_bytes(data)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(400)

    return HTTPServer(("127.0.0.1", 0), Handler)


def run_probe(binary, output, mode):
    """Use fresh configuration and retain the exact fixture and binary bindings."""
    if sys.platform != "darwin":
        raise ValueError("this probe requires the macOS outer sandbox")
    if mode not in MODES:
        raise ValueError("unknown probe mode")
    binary = Path(binary).resolve(strict=True)
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError("probe output must use a canonical path")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("workspace", "config", "tmp"):
        (output / name).mkdir()
    identity = {
        "binary": str(binary),
        "binary_sha256": common._sha(binary),
        "probe_sha256": common._sha(__file__),
        "shared_probe_sha256": common._sha(common.__file__),
        "mode": mode,
    }
    for source in (Path(__file__), Path(common.__file__)):
        (output / source.name).write_bytes(source.read_bytes())
    profile = output / "fixture.sb"
    profile.write_text(
        "(version 1)\n(allow default)\n(deny network*)\n"
        '(allow network-outbound (remote ip "localhost:*"))\n'
        "(allow network* (local unix-socket) (remote unix-socket))\n"
        "(deny file-write*)\n"
        + f'(allow file-write* (subpath {json.dumps(str(output))}) (literal "/dev/null"))\n'
    )
    settings = {}
    if mode != "baseline":
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": common._hook(output, mode), "timeout": 1}
                        ],
                    }
                ]
            }
        }
    common._write(output / "settings.json", settings)
    requests, errors = [], []
    server = _server(requests, errors, output)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(output / "tmp"),
        "CLAUDE_CONFIG_DIR": str(output / "config"),
        "CLAUDE_CODE_TMPDIR": str(output / "tmp"),
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "ANTHROPIC_API_KEY": "aisle-fixture-key",
        "DISABLE_AUTOUPDATER": "1",
    }
    command = ["/usr/bin/sandbox-exec", "-f", str(profile), str(binary)]
    try:
        version = subprocess.run(
            [*command, "--version"],
            env=env,
            cwd=output / "workspace",
            capture_output=True,
            text=True,
            timeout=10,
        )
        identity["version"] = version.stdout.strip()
        if version.returncode != 0 or identity["version"] != "2.1.263 (Claude Code)":
            raise ValueError("unsupported frontend revision for this probe profile")
        args = [
            *command,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--settings",
            str(output / "settings.json"),
            "--dangerously-skip-permissions",
            "--model",
            "sonnet",
            "--system-prompt",
            "Local infrastructure fixture.",
            "Write the fixture marker and finish.",
        ]
        common._write(
            output / "invocation.json",
            {"argv": args, "environment": env, "cwd": str(output / "workspace"), **identity},
        )
        identity["fixture_files"] = common._bind_fixture(
            output, mode, [Path(__file__).name, Path(common.__file__).name], ["settings.json"]
        )
        timed_out = False
        with (
            (output / "stdout.jsonl").open("xb") as stdout,
            (output / "stderr.log").open("xb") as stderr,
        ):
            process = subprocess.Popen(
                args,
                cwd=output / "workspace",
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        hook = output / "hook-input.json"
        marker = output / "workspace/marker.txt"
        evidence = {
            "rc": process.returncode,
            "timed_out": timed_out,
            "requests": requests,
            "hook": json.loads(hook.read_text()) if hook.exists() else None,
            "marker": marker.read_text() if marker.exists() else None,
        }
        common._write(output / "evidence.json", evidence)
        result = summarize_probe(evidence)
        result["errors"].extend(errors)
        result["errors"].extend(common._fixture_errors(output, identity["fixture_files"]))
        if common._sha(binary) != identity["binary_sha256"]:
            result["errors"].append("frontend binary changed during probing")
        result["ok"] = result["ok"] and not result["errors"]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {
            "ok": False,
            "complete_coverage": False,
            "confinement_verified": False,
            "error": str(exc),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result.update(
        schema_version="aisle.claude-dispatch-probe.v1",
        scope="synthetic_model_bash_probe",
        output=str(output),
        **identity,
    )
    common._write(output / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    args = parser.parse_args()
    try:
        result = run_probe(args.binary, args.output, args.mode)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {
            "ok": False,
            "complete_coverage": False,
            "confinement_verified": False,
            "error": str(exc),
        }
    print(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
