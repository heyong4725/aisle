"""Run admitted App Server sessions with controller-owned authorization and evidence."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from aisle.harness.frontend_app_server import dynamic_tools, run_app_server
from aisle.harness.frontend_dispatch import DispatchBudget
from aisle.harness.frontend_request_authority import RequestAuthority
from aisle.harness.matched_frontend import FrontendToolBudget
from aisle.harness.matched_tool_service import ToolService, _directory, _read, _write


def _json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def acquire_authority_evidence(output, references):
    """Acquire bounded snapshots against references held by the session controller."""
    if not references or not {"authority", "protocol"}.issubset(references):
        return None

    def snapshot(directory, expected, *, extra=()):
        fd = _directory(directory)
        try:
            names = expected["artifacts"]
            if set(os.listdir(fd)) != set(names) | set(extra):
                raise ValueError("authorization evidence inventory changed")
            result = {}
            total = 0
            for name in names:
                if Path(name).name != name:
                    raise ValueError("invalid authorization artifact name")
                data = _read(fd, name, 65536)
                if data is None:
                    raise ValueError("unfinished authorization evidence")
                total += len(data)
                if total > 4 * 1024 * 1024:
                    raise ValueError("authorization evidence exceeds acquisition limit")
                result[name] = data
            return result
        finally:
            os.close(fd)

    authority, protocol = references["authority"], references["protocol"]
    result = {
        "artifacts": snapshot(output / "frontend-authority", authority),
        "expected": authority,
        "byte_limit": 4 * 1024 * 1024,
        "protocol": {
            "artifacts": snapshot(output / "frontend-protocol", protocol, extra=("stderr.log",)),
            "expected": protocol,
            "byte_limit": 4 * 1024 * 1024,
        },
    }
    if "dispatch" in references:
        result["dispatch"] = {
            "artifacts": snapshot(output / "frontend-dispatch", references["dispatch"]),
            "expected": references["dispatch"],
            "byte_limit": 4 * 1024 * 1024,
        }
    return result


def run_authorized_app_server(controller, command, *, cwd, env, launch, budget, references):
    """Host the fixed harness request API while the owned frontend process runs."""
    output = controller.output
    manifest = controller.plan["arms"][controller.arm]
    allowed = manifest["policy"]["allowed_external_tools"]
    operations = [op for op in ("check", "run") if "harness." + op in allowed]
    thread = {
        **launch["app_server"],
        "cwd": str(cwd),
        "ephemeral": True,
        "model": manifest["model"]["requested_identity"],
        "approvalPolicy": manifest["policy"]["approval"],
        "sandbox": "danger-full-access",
        "dynamicTools": dynamic_tools(operations),
    }
    guard = (
        FrontendToolBudget("codex_app_server", budget["frontend_tool_ceiling"])
        if "frontend_tool_ceiling" in budget
        else None
    )
    deadline = time.monotonic() + budget["wall_ceiling_s"]
    protocol_root = output / "frontend-protocol"
    primary_error = None
    dispatch = None
    try:
        with (
            RequestAuthority(
                output / "frontend-authority", session_id=controller.session_id
            ) as authority,
            (
                DispatchBudget(
                    output / "frontend-dispatch",
                    session_id=controller.session_id,
                    ceiling=budget["frontend_tool_ceiling"],
                )
                if "frontend_tool_ceiling" in budget
                else nullcontext(None)
            ) as dispatch,
        ):
            with ToolService(controller, request_authority=authority) as service:

                def request(call, source):
                    if call["tool_name"] not in allowed:
                        raise ValueError("frontend requested an unadmitted harness tool")
                    operation = call["tool_name"].split(".")[1]
                    identity = uuid.uuid4().hex
                    request = {
                        "schema_version": "aisle.matched-tool-request.v1",
                        "id": identity,
                        "operation": operation,
                    }
                    raw = json.dumps(request).encode() + b"\n"
                    grant = authority.authorize(call=call, request=raw)
                    fd = _directory(service.channel)
                    try:
                        _write(
                            fd,
                            f"{identity}.authorization.json",
                            {"authorization_id": grant["authorization_id"]},
                        )
                        _write(fd, f"{identity}.request.json", request)
                        while time.monotonic() < deadline:
                            if service.failed.is_set():
                                raise ValueError(
                                    "authorized tool service failed: " + str(service.error)
                                )
                            response = service.controller_response(identity)
                            if response is not None:
                                return {
                                    "success": response["ok"],
                                    "contentItems": [
                                        {
                                            "type": "inputText",
                                            "text": json.dumps(response, allow_nan=False),
                                        }
                                    ],
                                }
                            time.sleep(0.01)
                        raise TimeoutError("authorized tool response deadline expired")
                    finally:
                        os.close(fd)

                def handle(call, source):
                    if dispatch is None:
                        return request(call, source)
                    response = None

                    def deliver(frame):
                        nonlocal response
                        response = request(call, frame)

                    dispatch.dispatch(call, source, deliver)
                    return response

                result = run_app_server(
                    command,
                    cwd=cwd,
                    env=env,
                    output=protocol_root,
                    thread_params=thread,
                    input_items=[
                        {
                            "type": "text",
                            "text": "Perform the assigned research task.",
                            "text_elements": [],
                        }
                    ],
                    handle_call=handle,
                    timeout_s=budget["wall_ceiling_s"],
                    token_ceiling=budget["ceiling"],
                    on_message=guard,
                )
            if not service.report["ok"]:
                raise ValueError("authorized tool service did not finish cleanly")
        references.update(
            authority=authority.reference(),
            protocol={
                key: result[key] for key in ("thread_id", "turn_id", "dynamic_calls", "artifacts")
            },
        )
        _json(output / "frontend-authority-reference.json", references["authority"])
        _json(output / "frontend-protocol-reference.json", references["protocol"])
    except BaseException as exc:
        primary_error = exc
        raise
    finally:

        def retain_dispatch():
            if dispatch is not None:
                references["dispatch"] = dispatch.reference()
                _json(output / "frontend-dispatch-reference.json", references["dispatch"])

        def retain_protocol():
            if protocol_root.is_dir():
                with (output / "session.jsonl").open("xb") as transcript:
                    for path in sorted(protocol_root.glob("*-received.json")):
                        transcript.write(path.read_bytes())
                stderr = protocol_root / "stderr.log"
                if stderr.is_file():
                    (output / "session.stderr").write_bytes(stderr.read_bytes())

        def retain_observations():
            if guard is not None:
                _json(output / "frontend-live.json", guard.report())

        failure = primary_error
        for retain in (retain_dispatch, retain_protocol, retain_observations):
            try:
                retain()
            except BaseException as cleanup_error:
                if failure is None:
                    failure = cleanup_error
                else:
                    failure.add_note(f"frontend finalization failed: {cleanup_error}")
        if primary_error is None and failure is not None:
            raise failure
    _json(
        output / "token_samples.jsonl",
        {
            "tokens": result["tokens"],
            "tokens_generated": result["tokens_generated"],
            "wall_s": result["wall_s"],
        },
    )
    process = {
        key: result[key]
        for key in ("rc", "tokens", "tokens_generated", "wall_s", "stopped", "stream_complete")
    }
    process["artifacts"] = {
        name: "sha256:" + hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in ("session.jsonl", "session.stderr", "token_samples.jsonl")
    }
    _json(output / "session-record.json", process)
    return process
