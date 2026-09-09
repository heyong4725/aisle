"""MON-12/MON-13: exercise actual Codex dispatch against a scripted local provider.

This engineering probe measures one exec_command route with synthetic fallback
model metadata. It does not enforce budgets or attest complete frontend coverage.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

COMMAND = "printf fixture > marker.txt"
CALL_ID = "call_fixture"
MODES = (
    "baseline",
    "deny",
    "unsupported",
    "malformed",
    "timeout",
    "exit1",
    "missing_script",
    "missing_command",
)
LIMIT = 16 * 1024 * 1024


def summarize_probe(evidence):
    """Check process, reply, hook identity and side effect before classifying a probe."""
    result = {
        "ok": False,
        "tool_executed": None,
        "tool_blocked": None,
        "complete_coverage": False,
        "confinement_verified": False,
        "errors": [],
    }
    errors = result["errors"]
    if evidence["rc"] != 0 or evidence["timed_out"]:
        errors.append("frontend did not finish normally")
    requests = evidence["requests"]
    if len(requests) != 2 or any(row["authorization_present"] for row in requests):
        errors.append("fixture requires exactly two unauthenticated provider requests")
    replies = [item for row in requests for item in row["tool_outputs"]]
    if len(replies) != 1 or replies[0].get("call_id") != CALL_ID:
        errors.append("tool result identity or cardinality differs")
    hook = evidence["hook"]
    if hook is not None and (
        hook.get("hook_event_name") != "PreToolUse"
        or hook.get("tool_use_id") != CALL_ID
        or hook.get("tool_name") != "Bash"
        or hook.get("tool_input") != {"command": COMMAND}
    ):
        errors.append("hook identity or command differs")
    if not errors:
        output = replies[0].get("output")
        if not isinstance(output, str):
            errors.append("tool output is not text")
        else:
            blocked = output.startswith("Command blocked by PreToolUse hook:")
            if blocked:
                if evidence["marker"] is not None:
                    errors.append("denied tool still produced a side effect")
            elif "Process exited with code 0\n" not in output or evidence["marker"] != "fixture":
                errors.append("neither explicit denial nor successful side effect was observed")
            if not errors:
                result.update(ok=True, tool_executed=not blocked, tool_blocked=blocked)
    return result


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def _fixture_snapshot(output, required, absent):
    """Hash selected regular inputs; explicit absence is part of the fixture."""
    result = {}
    for name in [*required, *absent]:
        if not name or Path(name).name != name or name in {".", ".."} or name in result:
            raise ValueError("fixture names must be unique direct children")
        path = output / name
        if name in absent:
            if path.exists() or path.is_symlink():
                raise ValueError(f"fixture must be absent: {name}")
            result[name] = None
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > LIMIT:
                raise ValueError(f"fixture must be a bounded regular file: {name}")
            data = stream.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError(f"fixture exceeds size limit: {name}")
            result[name] = hashlib.sha256(data).hexdigest()
    return result


def _fixture_errors(output, expected):
    """Compare endpoints only; this cannot attest loaded code or transient drift."""
    errors = []
    for name, digest in expected.items():
        try:
            actual = _fixture_snapshot(output, [name] if digest else [], [] if digest else [name])
            if actual[name] != digest:
                errors.append(f"fixture changed: {name}")
        except (OSError, ValueError) as exc:
            errors.append(f"fixture invalid: {name}: {exc}")
    return errors


def _bind_fixture(output, mode, sources, extra=()):
    required = ["invocation.json", "fixture.sb", *sources, *extra]
    absent = ["no-such-command"]
    (absent if mode in {"baseline", "missing_script"} else required).append("hook.py")
    snapshot = _fixture_snapshot(output, required, absent)
    _write(output / "fixture-preflight.json", snapshot)
    return {**snapshot, "fixture-preflight.json": _sha(output / "fixture-preflight.json")}


def _events(first):
    item = {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "fixture done", "annotations": []}],
    }
    if first:
        item = {
            "id": "fc_fixture",
            "type": "function_call",
            "call_id": CALL_ID,
            "name": "exec_command",
            "arguments": json.dumps(
                {"cmd": COMMAND, "login": False, "yield_time_ms": 1000, "max_output_tokens": 1000}
            ),
        }
    response = {
        "id": "resp_fixture",
        "object": "response",
        "status": "completed",
        "output": [item],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    events = [
        {
            "type": "response.created",
            "response": {**response, "status": "in_progress", "output": []},
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _server(requests, errors, output):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                self.connection.settimeout(5)
                length = int(self.headers.get("Content-Length", "-1"))
                if self.path != "/v1/responses" or not 0 <= length <= LIMIT or len(requests) >= 2:
                    raise ValueError("unexpected request path, size or count")
                raw = self.rfile.read(length)
                encoding = self.headers.get("Content-Encoding")
                if encoding == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(LIMIT + 1)
                elif encoding is not None:
                    raise ValueError("unsupported request encoding")
                if len(raw) > LIMIT:
                    raise ValueError("decoded request exceeds fixture limit")
                body = json.loads(raw)
                selected = {
                    "path": self.path,
                    "model": body.get("model"),
                    "tools": body.get("tools", []),
                    "authorization_present": "Authorization" in self.headers,
                    "tool_outputs": [
                        item
                        for item in body.get("input", [])
                        if item.get("type") == "function_call_output"
                    ],
                }
                requests.append(selected)
                _write(output / f"request-{len(requests)}.json", selected)
                if selected["authorization_present"]:
                    raise ValueError("fixture refuses authenticated requests")
                data = _events(len(requests) == 1)
                (output / f"response-{len(requests)}.sse").write_bytes(data)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(400)

    # Serial requests make the two-response script deterministic; client retries
    # or overlapping requests cannot obtain additional tool instructions.
    server = HTTPServer(("127.0.0.1", 0), Handler)
    return server


def _hook(output, mode):
    hook = output / "hook.py"
    denial = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "AISLE fixture denial",
        }
    }
    tail = f"print({json.dumps(denial)!r})\n"
    if mode == "unsupported":
        tail = "print('{\"continue\":false}')\n"
    elif mode == "malformed":
        tail = "print('not JSON')\n"
    elif mode == "timeout":
        tail = "import time; time.sleep(3)\n" + tail
    elif mode == "exit1":
        tail = "raise SystemExit(1)\n"
    source = (
        "import json,sys\nfrom pathlib import Path\nvalue=json.load(sys.stdin)\n"
        f"Path({str(output / 'hook-input.json')!r}).write_text(json.dumps(value))\n" + tail
    )
    if mode != "missing_script":
        hook.write_text(source)
    if mode == "missing_command":
        return shlex.quote(str(output / "no-such-command"))
    return shlex.join([str(Path(sys.executable).resolve()), str(hook)])


def run_probe(binary, output, mode, *, allow_pty=False):
    """Retain a bounded actual-CLI probe; never reuse an existing output directory."""
    if sys.platform != "darwin":
        raise ValueError("this probe requires the macOS outer sandbox")
    if mode not in MODES:
        raise ValueError("unknown probe mode")
    if type(allow_pty) is not bool:
        raise ValueError("PTY fixture selection must be a boolean")
    binary = Path(binary).resolve(strict=True)
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError("probe output must use a canonical path")
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    fixture_home = output / "codex-home"
    fixture_home.mkdir()
    temporary = output / "tmp"
    temporary.mkdir()
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(temporary),
        "CODEX_HOME": str(fixture_home),
    }
    profile = output / "fixture.sb"
    profile.write_text(
        "(version 1)\n(allow default)\n(deny network*)\n"
        '(allow network-outbound (remote ip "localhost:*"))\n'
        '(allow network-bind (local ip "localhost:*"))\n'
        '(allow network-inbound (local ip "localhost:*"))\n'
        "(allow network* (local unix-socket) (remote unix-socket))\n"
        "(deny file-write*)\n"
        f'(allow file-write* (subpath {json.dumps(str(output))}) (literal "/dev/null"))\n'
        + (
            '(allow file-write* (literal "/dev/ptmx") (regex #"^/dev/ttys[0-9]+$"))\n'
            if allow_pty
            else ""
        )
    )
    identity = {
        "binary": str(binary),
        "binary_sha256": _sha(binary),
        "probe_sha256": _sha(__file__),
        "mode": mode,
        "pty_enabled": allow_pty,
    }
    (output / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    requests, errors = [], []
    server = _server(requests, errors, output)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = ["/usr/bin/sandbox-exec", "-f", str(profile), str(binary)]
    try:
        version = subprocess.run(
            [*command, "--version"],
            env=env,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
        identity["version"] = version.stdout.strip()
        if version.returncode != 0 or identity["version"] != "codex-cli 0.153.4":
            raise ValueError("unsupported frontend revision for this probe profile")
        args = [
            *command,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(workspace),
            "-s",
            "danger-full-access",
            "-m",
            "aisle-fixture",
        ]
        configuration = {
            "sqlite_home": str(output / "state"),
            "log_dir": str(output / "logs"),
            "model_provider": "aisle_fixture",
            "model_providers.aisle_fixture.name": "AISLE local fixture",
            "model_providers.aisle_fixture.base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model_providers.aisle_fixture.requires_openai_auth": False,
            "model_providers.aisle_fixture.supports_websockets": False,
            "model_providers.aisle_fixture.request_max_retries": 0,
            "model_providers.aisle_fixture.stream_max_retries": 0,
            "model_providers.aisle_fixture.stream_idle_timeout_ms": 5000,
        }
        for key, value in configuration.items():
            args.extend(["-c", f"{key}={json.dumps(value)}"])
        if mode != "baseline":
            hook = _hook(output, mode)
            args.extend(
                [
                    "--dangerously-bypass-hook-trust",
                    "-c",
                    "features.hooks=true",
                    "-c",
                    'hooks.PreToolUse=[{matcher="Bash",hooks=[{type="command",timeout=1,command='
                    + json.dumps(hook)
                    + "}]}]",
                ]
            )
        args.append("Local infrastructure fixture. Return fixture done.")
        _write(
            output / "invocation.json",
            {"argv": args, "environment": env, "cwd": str(workspace), **identity},
        )
        identity["fixture_files"] = _bind_fixture(output, mode, [Path(__file__).name])
        timed_out = False
        with (
            (output / "stdout.jsonl").open("xb") as stdout,
            (output / "stderr.log").open("xb") as stderr,
        ):
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=env,
                cwd=workspace,
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
        hook_path = output / "hook-input.json"
        marker = workspace / "marker.txt"
        evidence = {
            "rc": process.returncode,
            "timed_out": timed_out,
            "requests": requests,
            "hook": json.loads(hook_path.read_text()) if hook_path.exists() else None,
            "marker": marker.read_text() if marker.exists() else None,
        }
        _write(output / "evidence.json", evidence)
        result = summarize_probe(evidence)
        result["errors"].extend(errors)
        result["errors"].extend(_fixture_errors(output, identity["fixture_files"]))
        if _sha(binary) != identity["binary_sha256"]:
            result["errors"].append("frontend binary changed during probing")
        result["ok"] = result["ok"] and not result["errors"]
        result.update(
            schema_version="aisle.codex-dispatch-probe.v1",
            scope="synthetic_model_exec_command_probe",
            output=str(output),
            **identity,
        )
        _write(output / "report.json", result)
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {
            "schema_version": "aisle.codex-dispatch-probe.v1",
            "ok": False,
            "complete_coverage": False,
            "confinement_verified": False,
            "error": str(exc),
            **identity,
        }
        if not (output / "report.json").exists():
            _write(output / "report.json", result)
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument(
        "--allow-pty", action="store_true", help="allow PTY device writes in the fixture"
    )
    args = parser.parse_args()
    try:
        result = run_probe(args.binary, args.output, args.mode, allow_pty=args.allow_pty)
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
