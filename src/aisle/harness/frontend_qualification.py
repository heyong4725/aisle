"""Semantic replay of controller-bound frontend conformance session evidence.

Receipt acquisition belongs to frontend_conformance. A successful replay here
establishes execution evidence, not exhaustive route coverage or confinement.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath

from aisle.harness.frontend_conformance import (
    MAX_INPUT_BYTES,
    MAX_SESSION_BYTES,
    MAX_SESSION_FILES,
    _constant,
    _object,
)
from aisle.harness.matched_evidence import audit_tool_journal


def _json(raw):
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)


def _bounded_artifacts(artifacts):
    if (
        type(artifacts) is not dict
        or len(artifacts) > MAX_SESSION_FILES
        or any(type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES for raw in artifacts.values())
        or sum(map(len, artifacts.values())) > MAX_SESSION_BYTES
    ):
        raise ValueError("unbounded qualification artifacts")
    for name in artifacts:
        _name(name)


def completed_route_items(artifacts):
    """Classify completed work AFTER source replay; these are observations, not coverage
    verdicts.
    """
    from aisle.harness.frontend_app_server import AppServerScope
    from aisle.harness.frontend_conformance import ROUTE_UNITS
    from aisle.harness.provider_response_authority import _events

    routes = {name: [] for name in ROUTE_UNITS}
    protocol = _json(artifacts["frontend-protocol-reference.json"])
    dispatch = _json(artifacts["frontend-dispatch-reference.json"])
    scope = AppServerScope(protocol["thread_id"], protocol["turn_id"])
    calls, nested = {}, []
    for number in range(1, dispatch["attempts"] + 1):
        prefix = f"frontend-dispatch/{number:08d}"
        reservation = _json(artifacts[prefix + "-reservation.json"])
        if reservation["decision"] != "authorized":
            continue
        call = reservation["call"]
        if call["turn_id"].startswith("code-mode:"):
            from aisle.harness.code_mode_authority import _decode
            from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES

            message = _decode("ToolCall", artifacts[prefix + ".frame"], MAX_FRAME_BYTES)
            nested.append((call, _json(message.input_json)))
        elif call["tool_name"].startswith("["):
            events = list(_events(artifacts[prefix + ".frame"]))
            if len(events) != 1 or events[0][1] is None:
                raise ValueError("ambiguous provider route source")
            item = events[0][1]["item"]
            if call["call_id"] in calls:
                raise ValueError("ambiguous provider call identity")
            calls[call["call_id"]] = item
    hosted = set()
    for number in range(1, dispatch.get("hosted_requests", 0) + 1):
        settlement = _json(artifacts[f"frontend-dispatch/hosted-{number:08d}-settlement.json"])
        if settlement["status"] == "completed":
            hosted.update(call["call_id"] for call in settlement["calls"])
    commands = []
    for name in sorted(artifacts):
        if not re.fullmatch(r"frontend-protocol/[0-9]{8}-received.json", name):
            continue
        event = _json(artifacts[name])
        if "method" not in event:
            continue
        params = event.get("params", {})
        item = params.get("item", {})
        if event["method"] == "item/completed" and item.get("type") in {
            "commandExecution",
            "fileChange",
            "mcpToolCall",
            "webSearch",
        }:
            scope._identity(params.get("threadId"), params.get("turnId"))
            identity = {
                "call_id": item["id"],
                "thread_id": params["threadId"],
                "turn_id": params["turnId"],
            }
            source = calls.get(item["id"], {})
            observed = []
            nested_observed = False
            if (
                item["type"] == "commandExecution"
                and item.get("status") == "completed"
                and type(item.get("exitCode")) is int
                and item["exitCode"] == 0
            ):
                commands.append((identity, item))
                if source.get("name") == "exec_command":
                    observed.append("native")
                matches = [
                    (call, payload)
                    for call, payload in nested
                    if call["tool_name"] == "exec_command"
                    and type(payload) is dict
                    and type(payload.get("cmd")) is str
                    and any(
                        action.get("command") == payload["cmd"]
                        for action in item.get("commandActions", [])
                    )
                ]
                if len(matches) == 1:
                    routes["nested"].append(
                        {**identity, "runtime_call_id": matches[0][0]["call_id"]}
                    )
                    nested_observed = True
            elif (
                item["type"] == "fileChange"
                and item.get("status") == "completed"
                and item.get("changes")
                and source.get("name") == "apply_patch"
            ):
                observed.append("native_edit")
            elif (
                item["type"] == "mcpToolCall"
                and item.get("status") == "completed"
                and item.get("error") is None
                and type(item.get("result")) is dict
                and (
                    (
                        source.get("namespace") == "mcp__" + item["server"]
                        and source.get("name") == item["tool"]
                    )
                    or (
                        item["server"] == "aisle_harness"
                        and "mcp-harness-reference.json" in artifacts
                    )
                )
            ):
                observed.append("mcp")
            elif item["type"] == "webSearch" and item["id"] in hosted:
                observed.append("hosted")
            for route in observed:
                if route in routes:
                    routes[route].append(identity)
            if (observed or nested_observed) and params["threadId"] != scope.root:
                routes["subagents"].append(identity)
        scope.feed(event)
    outputs = {}
    for name, raw in artifacts.items():
        if re.fullmatch(r"provider/[0-9]{8}-request.body", name):
            metadata = _json(artifacts[name.removesuffix("body") + "json"])
            if metadata.get("content_encoding") != "identity":
                continue
            for item in _json(raw).get("input", []):
                if item.get("type") == "function_call_output":
                    identity, value = item.get("call_id"), item.get("output")
                    if identity in outputs and outputs[identity] != value:
                        raise ValueError("provider tool output changed across requests")
                    outputs[identity] = value
    for call_id, call in calls.items():
        if call.get("name") != "write_stdin":
            continue
        args = _json(call["arguments"])
        result = outputs.get(call_id)
        if (
            type(args.get("session_id")) is int
            and type(args.get("chars")) is str
            and args["chars"]
            and type(result) is str
            and re.search(r"(?m)^Process exited with code 0\r?$", result)
        ):
            matches = [
                identity
                for identity, item in commands
                if item.get("processId") == str(args["session_id"])
                and type(outputs.get(identity["call_id"])) is str
                and f"Process running with session ID {args['session_id']}\n"
                in outputs[identity["call_id"]]
            ]
            if len(matches) == 1:
                routes["continued_input"].append({**matches[0], "input_call_id": call_id})
    for line in artifacts["tool-events.jsonl"].splitlines():
        event = _json(line)
        record = event.get("record", {})
        process = record.get("process")
        if (
            record.get("classification") == "tool_result"
            and type(process) is dict
            and type(process.get("rc")) is int
            and process.get("timed_out") is False
        ):
            routes["harness"].append(
                {"attempt_id": record["immutable_id"], "operation": record["operation"]}
            )
    return routes


def _require_available_session(record):
    process = record.get("process")
    postflight = record.get("postflight")
    if (
        record.get("ok") is not True
        or record.get("error") is not None
        or record.get("classification") != "engineering_execution"
        or type(process) is not dict
        or type(process.get("rc")) is not int
        or process["rc"] != 0
        or process.get("stream_complete") is not True
        or process.get("stopped") not in (None, "agent_done")
        or type(postflight) is not dict
        or postflight.get("classification") != "synthetic_pass"
    ):
        raise ValueError("qualification requires an intact completed engineering session")


def link_nested_harness_results(artifacts, *, prefix, callbacks):
    """Join replayed host callbacks and owned pipe replies to audited controller attempts.

    Callers must first authenticate all bytes and replay RPC, protocol and service
    evidence. A controller request ID links the boundaries without equating their
    unrelated runtime and frontend call IDs.
    """
    from aisle.harness.frontend_effects import _same_json
    from aisle.harness.matched_app_server import controller_reply

    try:
        index = [_json(raw) for raw in artifacts.get("tool-request-index.jsonl", b"").splitlines()]
        results, used = [], set()
        for callback in callbacks:
            output = _json(callback["output_json"])
            mcp = callback["tool_name"] in {"mcp__aisle_harness.check", "mcp__aisle_harness.run"}
            if mcp:
                if (
                    type(output) is not dict
                    or set(output) != {"content", "isError"}
                    or type(output["isError"]) is not bool
                    or type(output["content"]) is not list
                    or len(output["content"]) != 1
                    or type(output["content"][0]) is not dict
                    or set(output["content"][0]) != {"type", "text"}
                    or output["content"][0]["type"] != "text"
                ):
                    raise ValueError("invalid nested MCP result envelope")
                text = output["content"][0]["text"]
            else:
                text = output
            if type(text) is not str:
                raise ValueError("nested harness result is not controller response text")
            response = _json(text)
            links = [link for link in index if link["request_id"] == response["request_id"]]
            if len(links) != 1 or response["request_id"] in used:
                raise ValueError("nested callback lacks a unique controller request")
            link = links[0]
            used.add(link["request_id"])
            attempt = _json(artifacts[f"tool-{link['attempt']:06d}/attempt.json"])
            expected = {
                key: attempt[key] for key in ("ok", "classification", "result", "error", "attempt")
            }
            expected["request_id"] = link["request_id"]
            visible = _json(
                controller_reply(expected, request_id=link["request_id"])["contentItems"][0]["text"]
            )
            call = link["frontend_authorization"]["call"]
            if (
                not _same_json(response, visible)
                or attempt["immutable_id"] != link["attempt_id"]
                or call["tool_name"] != "harness." + attempt["operation"]
                or callback["tool_name"]
                != ("mcp__aisle_harness." if mcp else "harness.") + attempt["operation"]
            ):
                raise ValueError("nested result differs from its audited controller attempt")
            if mcp:
                _match_nested_mcp_completion(prefix, callback, call, output, expected)
            else:
                calls = [
                    (wire, raw) for wire, candidate, raw in prefix["calls"] if candidate == call
                ]
                if len(calls) != 1 or not _same_json(
                    callback["arguments"], _json(calls[0][1])["params"]["arguments"]
                ):
                    raise ValueError("nested arguments differ from the owned harness request")
                wire, _ = calls[0]
                replies = [
                    row
                    for row in prefix["sent"]
                    if type(row.get("id")) is type(wire) and row["id"] == wire
                ]
                expected_reply = {
                    "id": wire,
                    "result": {
                        "success": expected["ok"],
                        "contentItems": [{"type": "inputText", "text": text}],
                    },
                }
                if len(replies) != 1 or not _same_json(replies[0], expected_reply):
                    raise ValueError("nested completion differs from its owned pipe reply")
            results.append(
                {key: callback[key] for key in ("session_id", "invocation_id", "runtime_call_id")}
                | {
                    "call_id": call["call_id"],
                    "turn_id": call["turn_id"],
                    "request_id": link["request_id"],
                    "attempt_id": link["attempt_id"],
                }
            )
        return results
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid nested harness result linkage") from exc


def _match_nested_mcp_completion(prefix, callback, call, output, expected):
    """Compare host output with the separately replayed owned MCP lifecycle."""
    from aisle.harness.frontend_effects import _same_json

    key = (call["turn_id"], call["call_id"])
    source = _json(prefix["mcp_sources"][key])["params"]
    item = source["item"]
    completed = prefix["mcp_completions"][key]
    if (
        source["turnId"] != call["turn_id"]
        or item["id"] != call["call_id"]
        or item["type"] != "mcpToolCall"
        or item["server"] != "aisle_harness"
        or callback["tool_name"] != "mcp__aisle_harness." + item["tool"]
        or not _same_json(callback["arguments"], item["arguments"])
        or any(
            not _same_json(completed.get(field), item[field])
            for field in ("type", "id", "server", "tool", "arguments")
        )
        or item["status"] != "inProgress"
        or completed["status"] != ("completed" if expected["ok"] else "failed")
        or completed["error"] is not None
        or output["isError"] is not (not expected["ok"])
        or not _same_json(completed["result"]["content"], output["content"])
    ):
        raise ValueError(
            "nested MCP completion differs from its owned source and controller result"
        )


def _nested_harness_evidence(proof, *, prefix=None, nested_report=None, refusal=False):
    from aisle.harness.code_mode_audit import verify_code_mode_sources
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix

    authority = _authority_evidence(proof["artifacts"], refusal=refusal)
    if prefix is None:
        prefix = read_protocol_prefix(**authority["protocol"])
    if nested_report is None:
        nested_report = verify_code_mode_sources(
            **authority["code_mode"], dispatch=authority["dispatch"]
        )
    if not nested_report["ok"]:
        raise ValueError("nested source replay failed before result linkage")
    return link_nested_harness_results(
        proof["artifacts"], prefix=prefix, callbacks=nested_report["delegated_calls"]
    )


def available_route_evidence(proof):
    """Require an intact engineering session before interpreting completed route work."""
    _require_available_session(proof["record"])
    report = audit_proof_execution(proof)
    routes = completed_route_items(proof["artifacts"])
    nested_links = (
        _nested_harness_evidence(proof) if "code-mode-reference.json" in proof["artifacts"] else []
    )
    return {
        "execution_audit": report,
        "routes": routes,
        "nested_harness_links": nested_links,
        "complete_coverage": False,
    }


def read_bound_scenario(proof):
    """Read scenario intent only from the original verified prelaunch fixture inputs."""
    from aisle.harness.frontend_conformance import ROUTE_UNITS, verify_profile_inputs
    from aisle.harness.frontend_effects import command_probe

    try:
        arm = proof["record"]["arm"]
        admitted = proof["admission"]["arms"][arm]
        launch = proof["admission"]["launch_bindings"][arm]
        artifacts = proof["artifacts"]
        files = {
            name.removeprefix("conformance/inputs/"): raw
            for name, raw in artifacts.items()
            if name.startswith("conformance/inputs/")
        }
        profile = verify_profile_inputs(
            artifacts["conformance/profile.json"],
            files,
            reference=launch["conformance_profile"],
            candidate=admitted,
            launch=launch,
            arm=arm,
            execution=proof["admission"],
        )
        name = "fixtures/scenario.json"
        if name not in profile["fixture_files"]:
            raise ValueError("scenario was not an admitted fixture input")
        scenario = _json(files[name])
        if (
            type(scenario) is not dict
            or scenario.get("schema_version") != "aisle.frontend-conformance-scenario.v1"
            or scenario.get("arm") != arm
            or scenario.get("route") not in ROUTE_UNITS
            or scenario.get("case") not in {"available", "quota_refused"}
        ):
            raise ValueError("invalid bound scenario identity")
        route, selector = scenario["route"], scenario["selector"]
        fields = {"schema_version", "route", "arm", "case", "selector"}
        if route == "harness":
            fields.add("operation")
            if (
                scenario.get("operation") not in {"check", "run"}
                or "harness." + scenario["operation"]
                not in admitted["policy"]["allowed_external_tools"]
            ):
                raise ValueError("scenario harness operation is not admitted")
        else:
            fields.update({"target", "marker"})
            command_probe(scenario["target"], scenario["marker"])
            if scenario["target"] not in admitted["repository"]["editable_allowlist"]:
                raise ValueError("scenario effect target is not admitted")
        if set(scenario) != fields or type(selector) is not dict:
            raise ValueError("invalid scenario fields")
        if route == "hosted":
            if (
                set(selector) != {"request_id"}
                or type(selector["request_id"]) is not str
                or re.fullmatch(r"[0-9]{8}", selector["request_id"]) is None
                or int(selector["request_id"]) == 0
            ):
                raise ValueError("invalid hosted scenario selector")
        else:
            expected = {"attempt", "startup_attempt"} if route == "continued_input" else {"attempt"}
            if (
                set(selector) != expected
                or any(
                    type(value) is not int or not 1 <= value <= 10000 for value in selector.values()
                )
                or route == "continued_input"
                and selector["startup_attempt"] >= selector["attempt"]
            ):
                raise ValueError("invalid local scenario selector")
        return scenario
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid bound scenario") from exc


def verify_original_launch(proof):
    """Bind an already-authenticated session's actual launch to admission and its listener.

    Provider, nested-host and controller MCP endpoints must have retained
    ownership descriptors. This does not attest OS confinement.
    """
    from aisle.harness.frontend_app_server import dynamic_tools
    from aisle.harness.frontend_effects import _same_json
    from aisle.harness.provider_runner import provider_settings
    from aisle.harness.provider_source_audit import read_provider_listener, verify_listener_identity

    try:
        record, admission, artifacts = proof["record"], proof["admission"], proof["artifacts"]
        arm = record["arm"]
        manifest, bound = admission["arms"][arm], admission["launch_bindings"][arm]
        actual = _json(artifacts["frontend-protocol/invocation.json"])
        launch = _json(artifacts["launch.json"])
        capability = _json(artifacts["capability.json"])
        roots = admission["confinement_bindings"][arm]["policy"]["visible_roots"]
        ambient = admission["ambient_bindings"][arm]
        budget = manifest["budget"]
        profile_hash = hashlib.sha256(artifacts["launch-profile.sb"]).hexdigest()
        if (
            type(roots) is not list
            or len(roots) != 1
            or launch["cwd"] != roots[0]
            or actual["cwd"] != roots[0]
            or not _same_json(launch["argv"], bound["argv"])
            or not _same_json(launch["budget"], budget)
            or launch["compiled_profile_sha256"] != profile_hash
            or manifest["confinement"]["profile_sha256"] != profile_hash
            or capability["adapter"]["sha256"] != manifest["confinement"]["adapter_binary_sha256"]
            or launch["environment_sha256"] != ambient["record"]["environment_sha256"]
            or actual["environment_sha256"]
            != hashlib.sha256(
                json.dumps(ambient["environment"], sort_keys=True).encode()
            ).hexdigest()
            or not _same_json(actual["timeout_s"], budget["wall_ceiling_s"])
        ):
            raise ValueError("actual launch differs from admitted process inputs")
        wrapped = launch["wrapped_argv"]
        if (
            type(wrapped) is not list
            or len(wrapped) < 4
            or wrapped[:2] != [capability["adapter"]["path"], "-f"]
            or type(wrapped[2]) is not str
            or not PurePosixPath(wrapped[2]).is_absolute()
            or str(PurePosixPath(wrapped[2])) != wrapped[2]
            or ".." in PurePosixPath(wrapped[2]).parts
            or PurePosixPath(wrapped[2]).name != "launch-profile.sb"
            or wrapped[3:] != bound["argv"]
        ):
            raise ValueError("actual wrapper differs from the admitted adapter")
        expected_command = list(wrapped)
        if "provider" in bound:
            provider = {
                name.removeprefix("provider/"): raw
                for name, raw in artifacts.items()
                if name.startswith("provider/")
            }
            listener = read_provider_listener(provider, session_id=record["session_id"])
            for key, value in provider_settings(
                bound["provider"], (listener["host"], listener["port"]), budget["wall_ceiling_s"]
            ).items():
                expected_command.extend(["-c", key + "=" + json.dumps(value)])
        if bound.get("mcp_harness"):
            if "provider" not in bound:
                raise ValueError("controller MCP requires the bound provider runner")
            reference = _json(artifacts["mcp-harness-reference.json"])
            listener = verify_listener_identity(
                reference["listener"],
                schema="aisle.mcp-harness-listener.v1",
                session_id=record["session_id"],
            )
            expected_command.extend(
                [
                    "-c",
                    "mcp_servers.aisle_harness.url="
                    + json.dumps(f"http://{listener['host']}:{listener['port']}/mcp"),
                    "-c",
                    "mcp_servers.aisle_harness.enabled=true",
                ]
            )
        if "code_mode_host" in bound:
            reference = _json(artifacts["code-mode-reference.json"])
            listener = verify_listener_identity(
                reference["listener"],
                schema="aisle.code-mode-listener.v1",
                session_id=record["session_id"],
            )
            backend = reference["backend"]
            if (
                not _same_json(reference["host"], bound["code_mode_host"])
                or type(backend) is not str
                or re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", backend) is None
                or int(backend.rsplit(":", 1)[1]) > 65535
                or artifacts["code-mode/host.stdout"]
                not in {
                    ("http://" + backend + "\n").encode(),
                    ("grpc://" + backend + "\n").encode(),
                }
            ):
                raise ValueError("nested host differs from its bound executable or backend")
            expected_command.extend(
                [
                    "--code-mode-host",
                    f"http://{listener['host']}:{listener['port']}",
                    "-c",
                    "features.code_mode=true",
                ]
            )
        operations = [
            op
            for op in ("check", "run")
            if "harness." + op in manifest["policy"]["allowed_external_tools"]
        ]
        expected_thread = {
            **bound["app_server"],
            "cwd": roots[0],
            "ephemeral": True,
            "model": manifest["model"]["requested_identity"],
            "approvalPolicy": manifest["policy"]["approval"],
            "sandbox": "danger-full-access",
            "dynamicTools": dynamic_tools(operations),
        }
        if (
            not _same_json(actual["argv"], expected_command)
            or not _same_json(actual["thread_params"], expected_thread)
            or actual["input_items"]
            != [
                {"type": "text", "text": "Perform the assigned research task.", "text_elements": []}
            ]
        ):
            raise ValueError("actual frontend differs from the bound launch and listener")
        return {"launch_verified": True, "confinement_verified": False}
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid original launch evidence") from exc


def provider_call_owner(artifacts, *, attempt):
    """Join an already source-audited provider call to canonical Codex turn metadata.

    Metadata is emitted by the pinned frontend, not inferred from agent messages.
    The caller must authenticate the original closed provider and dispatch bytes.
    This establishes attribution within that capture, not OS confinement.
    """
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix
    from aisle.harness.provider_response_authority import _events

    try:
        if type(attempt) is not int or not 1 <= attempt <= 10000:
            raise ValueError("invalid provider ownership attempt")
        protocol = _json(artifacts["frontend-protocol-reference.json"])
        prefix = read_protocol_prefix(
            {
                name.removeprefix("frontend-protocol/"): raw
                for name, raw in artifacts.items()
                if name.startswith("frontend-protocol/")
            },
            expected=protocol,
            byte_limit=MAX_INPUT_BYTES,
        )
        root = (protocol["thread_id"], protocol["turn_id"])
        parents, turns = {root[0]: (None, None)}, {root}
        for event, _ in prefix["sequences"]["received"]:
            params = event.get("params", {})
            item = params.get("item", {})
            if (
                event.get("method") == "item/completed"
                and item.get("type") == "subAgentActivity"
                and item.get("kind") == "started"
            ):
                parents[item["agentThreadId"]] = (params["threadId"], params["turnId"])
            elif event.get("method") == "turn/started":
                turns.add((params["threadId"], params["turn"]["id"]))
        frame = artifacts[f"frontend-dispatch/{attempt:08d}.frame"]
        call = _json(artifacts[f"frontend-dispatch/{attempt:08d}-reservation.json"])["call"]
        events = list(_events(frame))
        if (
            len(events) != 1
            or events[0][1] is None
            or events[0][1].get("item", {}).get("call_id") != call["call_id"]
        ):
            raise ValueError("ownership frame differs from selected provider call")
        requests = []
        for name, raw in artifacts.items():
            match = re.fullmatch(r"provider/([0-9]{8})-response\.sse", name)
            if match:
                requests.extend(match[1] for source, _ in _events(raw) if source == frame)
        if len(requests) != 1:
            raise ValueError("provider call lacks a unique original request")
        request_id = requests[0]
        encoding = _json(artifacts[f"provider/{request_id}-request.json"])["content_encoding"]
        raw = artifacts[f"provider/{request_id}-request.body"]
        if encoding != "identity" or len(raw) > MAX_INPUT_BYTES:
            raise ValueError("unsupported provider ownership request encoding")
        flat = _json(raw)["client_metadata"]
        metadata = _json(flat["x-codex-turn-metadata"])
        fields = {
            "session_id": "session_id",
            "thread_id": "thread_id",
            "turn_id": "turn_id",
            "parent_thread_id": "x-codex-parent-thread-id",
            "parent_turn_id": "parent_turn_id",
            "root_turn_id": "root_turn_id",
        }
        for key, projection in fields.items():
            value = metadata.get(key)
            if (
                value is not None and (type(value) is not str or not 0 < len(value) <= 256)
            ) or value != flat.get(projection):
                raise ValueError("canonical request identity differs from its projection")
        identity = (metadata.get("thread_id"), metadata.get("turn_id"))
        parent = (metadata.get("parent_thread_id"), metadata.get("parent_turn_id"))
        if (
            metadata.get("request_kind") != "turn"
            or identity not in turns
            or metadata.get("session_id") != root[0]
            or metadata.get("root_turn_id") != root[1]
            or parents.get(identity[0]) != parent
        ):
            raise ValueError("provider request does not belong to the owned turn ancestry")
        return {
            "request_id": request_id,
            "thread_id": identity[0],
            "turn_id": identity[1],
            "parent_thread_id": parent[0],
            "parent_turn_id": parent[1],
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid provider request ownership evidence") from exc


def route_scenario_evidence(proof):
    """Compose one bound scenario's source, execution and effect evidence.

    Input must be acquired with read_session_proof. This is a case verdict,
    not complete-profile, original-launch or independent-confinement admission.
    """
    from aisle.harness.frontend_effects import (
        command_effect_evidence,
        continued_effect_evidence,
        edit_effect_evidence,
        hosted_effect_evidence,
        mcp_effect_evidence,
    )

    try:
        scenario = read_bound_scenario(proof)
        route, selector = scenario["route"], scenario["selector"]
        refused = scenario["case"] == "quota_refused"
        audit = audit_refusal_execution(proof) if refused else available_route_evidence(proof)
        artifacts = proof["artifacts"]
        if route == "subagents":
            owner = provider_call_owner(artifacts, attempt=selector["attempt"])
            if owner["parent_thread_id"] is None:
                raise ValueError("subagent scenario selects a root provider request")
            if not refused and not any(
                row["thread_id"] == owner["thread_id"] and row["turn_id"] == owner["turn_id"]
                for row in audit["routes"]["subagents"]
                if row["call_id"]
                == _json(
                    artifacts[f"frontend-dispatch/{selector['attempt']:08d}-reservation.json"]
                )["call"]["call_id"]
            ):
                raise ValueError("completed child effect differs from its request owner")
        if route == "hosted":
            if (
                refused
                and selector["request_id"] not in audit["source_audit"]["linked_hosted_requests"]
            ):
                raise ValueError("scenario selects an unaudited hosted refusal")
            call = None
        else:
            reservation = _json(
                artifacts[f"frontend-dispatch/{selector['attempt']:08d}-reservation.json"]
            )
            call = reservation["call"]
            if (
                refused
                and selector["attempt"] not in audit["source_audit"]["linked_local_attempts"]
            ):
                raise ValueError("scenario selects an unaudited local refusal")
            if route in {"native", "nested"} and call["turn_id"].startswith("code-mode:") != (
                route == "nested"
            ):
                raise ValueError("scenario selects a different execution boundary")

        if route == "harness":
            if call["tool_name"] != "harness." + scenario["operation"]:
                raise ValueError("scenario selects another harness operation")
            links = [
                _json(raw) for raw in artifacts.get("tool-request-index.jsonl", b"").splitlines()
            ]
            matches = [link for link in links if link["frontend_authorization"]["call"] == call]
            if refused:
                if matches:
                    raise ValueError("refused harness scenario consumed a grant")
                effect = {"effect": "not_dispatched"}
            else:
                if len(matches) != 1 or not any(
                    row["attempt_id"] == matches[0]["attempt_id"]
                    and row["operation"] == scenario["operation"]
                    for row in audit["routes"]["harness"]
                ):
                    raise ValueError("scenario lacks a completed matching harness attempt")
                records = [
                    _json(raw)["record"]
                    for raw in artifacts["tool-events.jsonl"].splitlines()
                    if "record" in _json(raw)
                    and _json(raw)["record"]["immutable_id"] == matches[0]["attempt_id"]
                ]
                if len(records) != 1 or type(records[0]["ok"]) is not bool:
                    raise ValueError("harness effect lacks one audited controller verdict")
                effect = {
                    "effect": "controller_result",
                    "attempt_id": matches[0]["attempt_id"],
                    "ok": records[0]["ok"],
                }
        else:
            verifier = {
                "native": command_effect_evidence,
                "nested": command_effect_evidence,
                "subagents": command_effect_evidence,
                "native_edit": edit_effect_evidence,
                "continued_input": continued_effect_evidence,
                "mcp": mcp_effect_evidence,
                "hosted": hosted_effect_evidence,
            }[route]
            effect = verifier(
                proof,
                **selector,
                target=scenario["target"],
                marker=scenario["marker"],
                refused=refused,
            )
            if not refused:
                rows = audit["routes"][route]
                if route == "hosted":
                    from aisle.harness.provider_hosted import hosted_frontend_items

                    items = hosted_frontend_items(
                        artifacts[f"provider/{selector['request_id']}-response.sse"]
                    )
                    matched = len(items) == 1 and any(
                        row["call_id"] == items[0]["id"] for row in rows
                    )
                else:
                    identity = {
                        "nested": "runtime_call_id",
                        "continued_input": "input_call_id",
                    }.get(route, "call_id")
                    matched = any(row.get(identity) == call["call_id"] for row in rows)
                if not matched:
                    raise ValueError("effect lacks its matching completed frontend route")
        return {
            "route": route,
            "case": scenario["case"],
            "selector": selector,
            **({"operation": scenario["operation"]} if route == "harness" else {}),
            "case_verified": True,
            "audit": audit,
            "effect": effect,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid route scenario evidence") from exc


def _name(value):
    if (
        type(value) is not str
        or not value
        or str(PurePosixPath(value)) != value
        or PurePosixPath(value).is_absolute()
        or ".." in PurePosixPath(value).parts
    ):
        raise ValueError("invalid qualification artifact path")
    return value


def _authority_evidence(artifacts, *, refusal=False, unused_host=False, expected_host_failure=None):
    """Reconstruct source snapshots from receipt-bound bytes, without source-tree reads."""

    def reference(name):
        value = json.loads(artifacts[name], object_pairs_hook=_object, parse_constant=_constant)
        if type(value) is not dict:
            raise ValueError("invalid qualification authority reference")
        return value

    def snapshot(directory, expected):
        contents = {}
        for name, digest in expected["artifacts"].items():
            if PurePosixPath(_name(name)).name != name:
                raise ValueError("invalid qualification source artifact")
            raw = artifacts[directory + "/" + name]
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("qualification source differs from closed reference")
            contents[name] = raw
        return {"artifacts": contents, "expected": expected, "byte_limit": MAX_INPUT_BYTES}

    authority = snapshot("frontend-authority", reference("frontend-authority-reference.json"))
    authority["protocol"] = snapshot(
        "frontend-protocol", reference("frontend-protocol-reference.json")
    )
    authority["dispatch"] = snapshot(
        "frontend-dispatch", reference("frontend-dispatch-reference.json")
    )
    for key, directory, name in (
        ("provider", "provider", "provider-reference.json"),
        ("mcp_harness", "mcp-harness", "mcp-harness-reference.json"),
    ):
        if name in artifacts:
            authority[key] = snapshot(directory, reference(name))
    if "code-mode-reference.json" in artifacts:
        host = reference("code-mode-reference.json")
        if host["proxy"] is None or (host["failure"] is not None and not (refusal or unused_host)):
            raise ValueError("qualification nested host did not complete")
        authority["code_mode"] = {
            **snapshot("code-mode/rpc", host["proxy"]),
            "delegated_tools": set(host["delegated_tools"]),
        }
        if host["failure"] is not None and (
            unused_host or refusal and not authority["code_mode"]["artifacts"]
        ):
            from aisle.harness.code_mode_audit import verify_code_mode_sources

            report = verify_code_mode_sources(
                **authority["code_mode"], dispatch=authority["dispatch"]
            )
            if (
                host["failure"]
                not in {
                    "ValueError: app-server turn failed",
                    "CancelledError: ",
                    "DispatchRefused: dispatch budget exhausted" if refusal else None,
                    expected_host_failure,
                }
                or any(name.startswith("code-mode/rpc/") for name in artifacts)
                or not report["ok"]
                or report["native_attempts"]
                or report["delegated_calls"]
            ):
                raise ValueError("native fault has an unrelated or active nested host failure")
            # The caller must still prove the bound fault or refusal and its
            # owned prefix. Empty host evidence proves no fault by itself.
            authority["unused_host_failure"] = host["failure"]
        elif host["failure"] is not None:
            from aisle.harness.code_mode_audit import verify_code_mode_hosted_refusal

            provider = authority["provider"]
            declared = _json(provider["artifacts"]["invocation.json"])["delegated_tools"]
            report = verify_code_mode_hosted_refusal(
                **authority["code_mode"],
                dispatch=authority["dispatch"],
                host_failure=host["failure"],
                provider=provider,
                provider_delegated_tools={tuple(row) for row in declared},
            )
            if not report["ok"]:
                raise ValueError("cancelled host refusal failed: " + "; ".join(report["errors"]))
            authority["failed_host_refusal"] = report
    return authority


def verify_worker_fixture(proof):
    """Join audited worker declarations to the original admitted fixture grants."""
    from aisle.harness.frontend_conformance import verify_profile_inputs

    try:
        artifacts, admission = proof["artifacts"], proof["admission"]
        arm = proof["record"]["arm"]
        attempts = [
            (name, _json(raw))
            for name, raw in artifacts.items()
            if re.fullmatch(r"tool-[0-9]{6}/attempt\.json", name)
        ]
        prepared = [
            (name, row) for name, row in attempts if row.get("worker_preparation") is not None
        ]
        if any(
            row.get("operation") == "run"
            and row.get("process") is not None
            and row.get("worker_preparation") is None
            for _, row in attempts
        ):
            raise ValueError("executed run lacks its worker declaration")
        if not prepared:
            return 0
        files = {
            name.removeprefix("conformance/inputs/"): raw
            for name, raw in artifacts.items()
            if name.startswith("conformance/inputs/")
        }
        launch = admission["launch_bindings"][arm]
        profile = verify_profile_inputs(
            artifacts["conformance/profile.json"],
            files,
            reference=launch["conformance_profile"],
            candidate=admission["arms"][arm],
            launch=launch,
            arm=arm,
            execution=admission,
        )
        fixture_name = "fixtures/worker-preparations.json"
        if fixture_name not in profile["fixture_files"]:
            raise ValueError("worker declarations were not an admitted fixture")
        fixture = _json(files[fixture_name])
        expected = [fixture[arm]] if arm == "typed" else fixture[arm]
        if type(expected) is not list:
            raise ValueError("worker fixture requires per-run declarations")

        def compact(value):
            if type(value) is dict:
                if value.get("schema_version") == "aisle.matched-runtime.v1":
                    if value != admission["tool_runtime"]:
                        raise ValueError("worker runtime differs from admission")
                    return {"runtime_id": value["immutable_id"]}
                return {key: compact(item) for key, item in value.items()}
            if type(value) is list:
                return [compact(item) for item in value]
            return value

        indices = set()
        for name, row in prepared:
            binding = row["worker_preparation"]
            index = binding["index"]
            if (
                row["operation"] != "run"
                or type(index) is not int
                or not 0 <= index < len(expected)
                or index in indices
            ):
                raise ValueError("worker fixture run index differs")
            indices.add(index)
            raw = artifacts[name.removesuffix("attempt.json") + "worker-declaration.json"]
            if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
                raise ValueError("worker declaration hash differs")
            if compact(_json(raw)) != expected[index]:
                raise ValueError("worker declaration differs from admitted fixture")
        return len(prepared)
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("worker fixture evidence is missing or invalid") from exc


def audit_proof_execution(proof):
    """Recompute the full frontend -> service -> attempt chain from acquired artifacts.

    The input must come from read_session_proof. This does not trust a recorded
    audit result and does not reinterpret failed sessions as intentional refusal.
    """
    return _audit_execution(proof, complete_frontend=True)


def _audit_execution(proof, *, complete_frontend, unused_host=False):
    try:
        artifacts = proof["artifacts"]
        _bounded_artifacts(artifacts)
        authority = _authority_evidence(
            artifacts,
            refusal=not complete_frontend,
            unused_host=unused_host and not complete_frontend,
        )
        if not complete_frontend:
            authority = {key: authority[key] for key in ("artifacts", "expected", "byte_limit")}
        record, admission = proof["record"], proof["admission"]
        with tempfile.TemporaryDirectory(prefix="aisle-conformance-") as temporary:
            output = Path(temporary).resolve()
            for name, raw in artifacts.items():
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(raw)
            report = audit_tool_journal(
                output,
                session_id=record["session_id"],
                plan_id=record["plan_id"],
                arm=record["arm"],
                development=admission.get("development"),
                request_authority=authority,
                require_frontend_source=complete_frontend,
                frontend_dispatch_ceiling=(
                    admission["arms"][record["arm"]]["budget"]["frontend_tool_ceiling"]
                    if complete_frontend
                    else None
                ),
            )
        required = ["ok", "service_verified", "frontend_authorization_verified"]
        if complete_frontend:
            required.extend(["frontend_source_verified", "frontend_reservation_verified"])
        if not all(report.get(key) is True for key in required):
            raise ValueError("qualification execution evidence failed: " + str(report.get("error")))
        if any(
            name not in artifacts or hashlib.sha256(artifacts[name]).hexdigest() != digest
            for name, digest in report["files"].items()
        ):
            raise ValueError("qualification audit read unbound artifact bytes")
        report["worker_fixtures_verified"] = verify_worker_fixture(proof)
        if complete_frontend:
            from aisle.harness.frontend_app_server_audit import read_protocol_prefix

            report["controller_replies"] = match_controller_replies(
                artifacts, read_protocol_prefix(**authority["protocol"])
            )
        return report
    except (OSError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("qualification source evidence is missing or invalid") from exc


def match_controller_replies(artifacts, prefix):
    """Join owned dynamic replies to independently audited service/attempt records."""
    from aisle.harness.frontend_effects import _same_json
    from aisle.harness.matched_app_server import controller_reply

    attempts = [
        _json(raw)["record"]
        for raw in artifacts["tool-events.jsonl"].splitlines()
        if "record" in _json(raw)
    ]
    links = [_json(raw) for raw in artifacts.get("tool-request-index.jsonl", b"").splitlines()]
    for wire, call, _ in prefix["calls"]:
        matches = [link for link in links if link["frontend_authorization"]["call"] == call]
        if len(matches) != 1:
            raise ValueError("frontend reply lacks one audited service request")
        link = matches[0]
        records = [attempt for attempt in attempts if attempt["immutable_id"] == link["attempt_id"]]
        if len(records) != 1:
            raise ValueError("frontend reply lacks one audited controller result")
        response = {
            key: records[0][key] for key in ("ok", "classification", "result", "error", "attempt")
        }
        response["request_id"] = link["request_id"]
        replies = [
            row.get("result")
            for row in prefix["sent"][4:]
            if type(row.get("id")) is type(wire) and row.get("id") == wire
        ]
        if len(replies) != 1 or not _same_json(
            replies[0], controller_reply(response, request_id=link["request_id"])
        ):
            raise ValueError("frontend reply differs from the audited controller result")
    return len(prefix["calls"])


def match_hosted_prefix_items(items, prefix):
    """Match provider results to a protocol prefix whose ownership was replayed."""
    from aisle.harness.provider_hosted import web_search_action

    expected = {item["id"]: item["action"] for item in items}
    if len(expected) != len(items):
        raise ValueError("duplicate hosted provider result")
    started, completed = {}, {}
    for row, _ in prefix["sequences"]["received"]:
        method = row.get("method")
        if method not in {"item/started", "item/completed"}:
            continue
        params = row["params"]
        item = params["item"]
        if item.get("type") != "webSearch":
            continue
        identity = item.get("id")
        if type(identity) is not str or not 0 < len(identity) <= 256:
            raise ValueError("invalid hosted frontend identity")
        value = {
            "thread": params["threadId"],
            "turn": params["turnId"],
            "action": web_search_action(item.get("action")),
        }
        if method == "item/started":
            if identity in started:
                raise ValueError("duplicate hosted frontend source")
            started[identity] = value
        else:
            if identity not in started or identity in completed or started[identity] != value:
                raise ValueError("unmatched hosted frontend completion")
            completed[identity] = value
    if (
        set(expected) != set(started)
        or set(expected) != set(completed)
        or any(completed[key]["action"] != action for key, action in expected.items())
    ):
        raise ValueError("hosted provider work differs from owned frontend lifecycle")
    return len(completed)


def match_provider_harness_calls(delegated_calls, prefix, *, nested=False, nested_links=()):
    """Match replayed provider delegation to scoped dynamic and controller MCP calls.

    Returns calls requiring a nested-host result link. MCP exceptions require
    links already verified by link_nested_harness_results; enabling the host alone
    grants no exception. Source authentication belongs to the caller.
    """
    try:
        provider_calls = [
            {key: row[key] for key in ("call_id", "namespace", "name", "payload")}
            for row in delegated_calls
            if row["namespace"] in {"harness", "mcp__aisle_harness"}
        ]
        remaining = [
            {
                "call_id": call["call_id"],
                "namespace": "harness",
                "name": call["tool_name"].removeprefix("harness."),
                "payload": _json(raw)["params"]["arguments"],
            }
            for _, call, raw in prefix["calls"]
        ]
        linked = [(row["turn_id"], row["call_id"]) for row in nested_links]
        if len(linked) != len(set(linked)):
            raise ValueError("duplicate nested result link")
        nested_mcp = []
        for key, raw in prefix["mcp_sources"].items():
            item = _json(raw)["params"]["item"]
            row = {
                "call_id": item["id"],
                "namespace": "mcp__" + item["server"],
                "name": item["tool"],
                "payload": item["arguments"],
            }
            remaining.append(row)
            if key in linked and row["namespace"] == "mcp__aisle_harness":
                nested_mcp.append(row)
        for call in provider_calls:
            matches = [
                row
                for row in remaining
                if json.dumps(row, sort_keys=True) == json.dumps(call, sort_keys=True)
            ]
            if len(matches) != 1:
                raise ValueError("provider delegation differs from owned harness calls")
            remaining.remove(matches[0])
        if any(
            not nested or (row["namespace"] != "harness" and row not in nested_mcp)
            for row in remaining
        ):
            raise ValueError("owned harness calls lack provider delegation")
        return remaining
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid provider harness linkage") from exc


def refusal_source_evidence(proof):
    """Bind all quota refusals to admitted identities and owned source paths.

    Input must be acquired by read_session_proof. This is the source part of
    qualification; successful execution chains, effects and scenario coverage
    remain separate and are not inferred from a refusal or failed process.
    """
    from aisle.harness.code_mode_audit import verify_code_mode_sources
    from aisle.harness.frontend_app_server_audit import (
        link_dynamic_refusal_sources,
        read_protocol_prefix,
    )
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal
    from aisle.harness.mcp_harness_source import link_mcp_refusal_sources, verify_mcp_sources
    from aisle.harness.provider_source_audit import (
        link_native_refusal_sources,
        verify_hosted_refusal_prefix,
        verify_provider_sources,
    )

    try:
        artifacts, record, admission = proof["artifacts"], proof["record"], proof["admission"]
        _bounded_artifacts(artifacts)
        authority = _authority_evidence(artifacts, refusal=True)
        dispatch = authority["dispatch"]
        arm = record["arm"]
        admitted, launch = admission["arms"][arm], admission["launch_bindings"][arm]
        ceiling = admitted["budget"]["frontend_tool_ceiling"]
        if (
            record["plan_id"] != admission["immutable_id"]
            or record["session_id"] != dispatch["expected"]["session_id"]
            or authority["expected"]["session_id"] != record["session_id"]
            or type(ceiling) is not int
            or ceiling != dispatch["expected"]["ceiling"]
        ):
            raise ValueError("refusal evidence differs from admitted session budget")
        prefix = read_protocol_prefix(**authority["protocol"])
        replay = verify_dispatch_journal(**dispatch)
        if not replay["ok"] or replay["delivery_uncertain"] or not replay["budget_refusals"]:
            raise ValueError("missing consistent quota refusal")
        local = {
            int(row["reservation"].removesuffix("-reservation.json")): row
            for row in replay["budget_refusals"]
            if row["kind"] == "local"
        }
        hosted = {row["request_id"] for row in replay["budget_refusals"] if row["kind"] == "hosted"}
        linked, linked_hosted, reports = set(), set(), {}

        def accept(name, report, field="refused_attempts"):
            if not report["ok"]:
                raise ValueError(name + " refusal source failed: " + "; ".join(report["errors"]))
            attempts = set(report[field]) & set(local)
            if linked & attempts:
                raise ValueError("refused source linked through overlapping paths")
            linked.update(attempts)
            reports[name] = report

        operations = [
            op
            for op in ("check", "run")
            if "harness." + op in admitted["policy"]["allowed_external_tools"]
        ]
        if any(
            row["call"]["tool_name"].startswith("harness.")
            and row["call"]["tool_name"] not in {"harness." + op for op in operations}
            for row in local.values()
        ):
            raise ValueError("refused harness route differs from admitted tool policy")
        delegated = {("harness", op, "function_call") for op in operations}
        if launch.get("mcp_harness"):
            delegated.update(("mcp__aisle_harness", op, "function_call") for op in operations)
        if "code_mode_host" in launch:
            delegated.update(
                {(None, "exec", "custom_tool_call"), ("functions", "exec", "custom_tool_call")}
            )
        # A host configuration is insufficient: acquire the actual MCP result
        # chain before permitting a missing direct provider delegation.
        nested_links = (
            _nested_harness_evidence(proof, prefix=prefix, refusal=True)
            if prefix["mcp_sources"] and "code_mode_host" in launch
            else []
        )
        provider_native = any(row["call"]["tool_name"].startswith("[") for row in local.values())
        if "provider" in launch or hosted or provider_native:
            provider = authority["provider"]
            invocation = _json(provider["artifacts"]["invocation.json"])
            if (
                invocation["binding"] != launch["provider"]
                or {tuple(tool) for tool in invocation["delegated_tools"]} != delegated
            ):
                raise ValueError("refusal provider differs from admitted binding")
            if hosted:
                report = verify_hosted_refusal_prefix(
                    **provider, dispatch=dispatch, delegated_tools=delegated
                )
                if not report["ok"]:
                    raise ValueError("hosted refusal source failed: " + "; ".join(report["errors"]))
                linked_hosted.update(report["linked_requests"])
                native = {
                    number
                    for number in range(1, dispatch["expected"]["attempts"] + 1)
                    if _json(dispatch["artifacts"][f"{number:08d}-reservation.json"])["call"][
                        "tool_name"
                    ].startswith("[")
                }
                if set(report["native_attempts"]) != native:
                    raise ValueError("hosted refusal has unmatched native reservations")
                report["unlinked_nested_harness_calls"] = match_provider_harness_calls(
                    report["delegated_calls"],
                    prefix,
                    nested="code_mode_host" in launch,
                    nested_links=nested_links,
                )
                reports["hosted"] = report
            if provider_native:
                report = link_native_refusal_sources(
                    **provider, dispatch=dispatch, delegated_tools=delegated
                )
                accept("provider", report)
                native = {
                    number
                    for number in range(1, dispatch["expected"]["attempts"] + 1)
                    if _json(dispatch["artifacts"][f"{number:08d}-reservation.json"])["call"][
                        "tool_name"
                    ].startswith("[")
                }
                if set(report["native_attempts"]) | set(report["refused_attempts"]) != native:
                    raise ValueError("provider refusal has unmatched native reservations")
                report["unlinked_nested_harness_calls"] = match_provider_harness_calls(
                    report["delegated_calls"],
                    prefix,
                    nested="code_mode_host" in launch,
                    nested_links=nested_links,
                )
            if not hosted and not provider_native:
                report = verify_provider_sources(
                    **provider, dispatch=dispatch, delegated_tools=delegated
                )
                if not report["ok"]:
                    raise ValueError("provider prefix failed: " + "; ".join(report["errors"]))
                native = {
                    number
                    for number in range(1, dispatch["expected"]["attempts"] + 1)
                    if _json(dispatch["artifacts"][f"{number:08d}-reservation.json"])["call"][
                        "tool_name"
                    ].startswith("[")
                }
                if set(report["native_attempts"]) != native:
                    raise ValueError("provider prefix has unmatched native reservations")
                report["unlinked_nested_harness_calls"] = match_provider_harness_calls(
                    report["delegated_calls"],
                    prefix,
                    nested="code_mode_host" in launch,
                    nested_links=nested_links,
                )
                reports["provider_prefix"] = report
            if "hosted_items" in report:
                report["completed_hosted_items"] = match_hosted_prefix_items(
                    report["hosted_items"], prefix
                )
        if any(row["call"]["turn_id"].startswith("code-mode:") for row in local.values()):
            if "code_mode_host" not in launch:
                raise ValueError("nested refusal has no admitted host")
            nested = authority["code_mode"]
            expected_delegates = {
                namespace + "." + name
                for namespace, name, kind in delegated
                if namespace in {"harness", "mcp__aisle_harness"} and kind == "function_call"
            }
            if nested["delegated_tools"] != expected_delegates:
                raise ValueError("nested delegation differs from admitted tool policy")
            accept(
                "nested", verify_code_mode_sources(**nested, dispatch=dispatch), "native_attempts"
            )
        dynamic = {(call["turn_id"], call["call_id"]) for _, call, _ in prefix["calls"]}
        if any(
            (row["call"]["turn_id"], row["call"]["call_id"]) in dynamic for row in local.values()
        ):
            accept(
                "dynamic", link_dynamic_refusal_sources(**authority["protocol"], dispatch=dispatch)
            )
        if prefix["mcp_sources"]:
            if not launch.get("mcp_harness"):
                raise ValueError("MCP source has no admitted harness route")
            if any(
                (row["call"]["turn_id"], row["call"]["call_id"]) in prefix["mcp_sources"]
                for row in local.values()
            ):
                attempts = link_mcp_refusal_sources(
                    **authority["mcp_harness"],
                    dispatch=dispatch,
                    sources=prefix["mcp_sources"],
                    completions=prefix["mcp_completions"],
                )
                accept("mcp", {"ok": True, "refused_attempts": attempts})
            else:
                verify_mcp_sources(
                    **authority["mcp_harness"],
                    sources=prefix["mcp_sources"],
                    completions=prefix["mcp_completions"],
                )
                reports["mcp_prefix"] = {"ok": True, "completed_calls": len(prefix["mcp_sources"])}
        if linked != set(local) or linked_hosted != hosted:
            raise ValueError("unmatched quota refusal source")
        return {
            "linked_local_attempts": sorted(linked),
            "linked_hosted_requests": sorted(linked_hosted),
            "source_audits": reports,
            "host_failure": authority.get("failed_host_refusal", {}).get("host_failure"),
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (OSError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("refusal source evidence is missing or invalid") from exc


def audit_concurrent_nested(proof):
    """Verify overlapping actual callbacks competing for one remaining reservation."""
    import base64

    from aisle.harness._code_mode_protocol import message
    from aisle.harness.frontend_effects import (
        command_probe,
        concurrent_probe,
        nested_execution,
        nested_observation_ms,
    )

    try:
        original = verify_original_launch(proof)
        case = route_scenario_evidence(proof)
        scenario = read_bound_scenario(proof)
        artifacts, record = proof["artifacts"], proof["record"]
        arm = record["arm"]
        ceiling = proof["admission"]["arms"][arm]["budget"]["frontend_tool_ceiling"]
        if type(ceiling) is not int or ceiling < 2:
            raise ValueError("invalid concurrent probe quota")
        competing = (ceiling, ceiling + 1)
        profile = _json(artifacts["conformance/profile.json"])
        if "fixtures/fault.json" not in profile["fixture_files"]:
            raise ValueError("concurrency fault was not bound before launch")
        fault = _json(artifacts["conformance/inputs/fixtures/fault.json"])
        if (
            fault
            != {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": "concurrent_nested",
                "route": "nested",
                "attempts": list(competing),
            }
            or case["route"] != "nested"
            or case["case"] != "quota_refused"
            or case["selector"] != {"attempt": competing[1]}
            or case["effect"]["effect"] != "unchanged"
            or type(record["ok"]) is not bool
            or record["eligible_for_estimate"] is not False
            or case["audit"]["authorized_harness_attempts"] != [1]
        ):
            raise ValueError(
                "concurrent case does not establish the bound successful-prefix refusal"
            )
        authority = _authority_evidence(artifacts, refusal=True)
        if record["ok"] is not True and "failed_host_refusal" not in authority:
            raise ValueError("failed concurrent host lacks a verified refusal prefix")
        dispatch = authority["dispatch"]
        if (
            dispatch["expected"]["attempts"] != competing[1]
            or dispatch["expected"]["reserved"] != ceiling
        ):
            raise ValueError("concurrent calls did not compete for the final slot")
        calls = [
            message("ToolCall", dispatch["artifacts"][f"{number:08d}.frame"])
            for number in competing
        ]
        first, second = calls
        command = command_probe(scenario["target"], scenario["marker"])
        arguments = ({"cmd": "sleep 1", "login": False}, {"cmd": command, "login": False})
        if (
            first.session_id != second.session_id
            or first.execution_id != second.execution_id
            or first.cell_id != second.cell_id
            or first.invocation_id == second.invocation_id
            or any(
                call.tool_name.name != "exec_command"
                or call.tool_name.namespace
                or _json(call.input_json) != args
                for call, args in zip(calls, arguments, strict=True)
            )
        ):
            raise ValueError("concurrent callback identities or commands differ")
        rows = [_json(raw) for _, raw in sorted(authority["code_mode"]["artifacts"].items())]
        received = []
        completions = []
        executions = []
        for position, row in enumerate(rows):
            raw = base64.b64decode(row["frame"], validate=True)
            if row["method"] == "SubscribeToToolCalls" and row["phase"] == "response":
                for index, number in enumerate(competing):
                    if raw == dispatch["artifacts"][f"{number:08d}.frame"]:
                        received.append((index, position))
            elif row["method"] == "CompleteToolCall" and row["phase"] == "request":
                value = message("CompleteToolCallRequest", raw)
                if (
                    value.session_id == first.session_id
                    and value.invocation_id == first.invocation_id
                ):
                    completions.append((position, value))
            elif row["method"] == "Execute" and row["phase"] == "request":
                executions.append(message("ExecuteRequest", raw))
        if (
            len(received) != 2
            or received[0][0] != 0
            or received[1][0] != 1
            or len(completions) != 1
            or not received[0][1] < received[1][1] < completions[0][0]
            or completions[0][1].WhichOneof("outcome") != "succeeded"
        ):
            raise ValueError("RPC order does not prove overlapping nested calls")
        result = _json(completions[0][1].succeeded.output_json)
        if (
            type(result.get("exit_code")) is not int
            or result["exit_code"] != 0
            or result.get("session_id") is not None
        ):
            raise ValueError("concurrent authorized command did not finish successfully")
        script = concurrent_probe(
            ceiling,
            mcp_harness=bool(proof["admission"]["launch_bindings"][arm].get("mcp_harness")),
            target=scenario["target"],
            marker=scenario["marker"],
        )
        wall_ceiling_s = proof["admission"]["arms"][arm]["budget"]["tool_wall_ceiling_s"]
        provider_script = nested_execution(script, wall_ceiling_s=wall_ceiling_s)
        reports = case["audit"]["source_audit"]["source_audits"]
        provider_prefix = reports["hosted"] if "hosted" in reports else reports["provider_prefix"]
        delegated = provider_prefix["delegated_calls"]
        outer = [
            call
            for call in delegated
            if call["name"] == "exec" and call["kind"] == "custom_tool_call"
        ]
        if (
            len(executions) != 1
            or executions[0].source != script
            or executions[0].yield_time_ms != nested_observation_ms(wall_ceiling_s)
            or executions[0].session_id != first.session_id
            or executions[0].execution_id != first.execution_id
            or len(outer) != 1
            or outer[0]["payload"] != provider_script
            or outer[0]["call_id"] != executions[0].tool_call_id
        ):
            raise ValueError("concurrent script differs between provider and owned nested host")
        attempts = [
            _json(raw)["record"]
            for raw in artifacts["tool-events.jsonl"].splitlines()
            if "record" in _json(raw)
        ]
        if len(attempts) != 1 or attempts[0]["ok"] is not True:
            raise ValueError("concurrent case lacks a successful treatment check")
        return {
            "fault_verified": True,
            "fault": "concurrent_nested",
            "overlap_verified": True,
            "effect": "unchanged",
            "launch": original,
            "authorized_attempts": list(range(1, ceiling + 1)),
            "refused_attempts": [competing[1]],
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid concurrent nested evidence") from exc


def _native_fault_delegations(candidate, launch, authority):
    """Verify unused optional routes before auditing a direct native fault prefix."""
    from aisle.harness.code_mode_audit import verify_code_mode_sources
    from aisle.harness.mcp_harness_source import verify_mcp_sources

    operations = [
        op
        for op in ("check", "run")
        if "harness." + op in candidate["policy"]["allowed_external_tools"]
    ]
    delegated = {("harness", op, "function_call") for op in operations}
    if ("code_mode" in authority) != ("code_mode_host" in launch) or (
        ("mcp_harness" in authority) != bool(launch.get("mcp_harness"))
    ):
        raise ValueError("native fault optional source inventory differs from admission")
    if launch.get("mcp_harness"):
        verify_mcp_sources(**authority["mcp_harness"], sources={}, completions={})
        delegated.update(("mcp__aisle_harness", op, "function_call") for op in operations)
    if "code_mode_host" in launch:
        nested = authority["code_mode"]
        expected = {namespace + "." + op for namespace, op, _ in delegated}
        if nested["delegated_tools"] != expected:
            raise ValueError("native fault nested delegation differs from admission")
        report = verify_code_mode_sources(**nested, dispatch=authority["dispatch"])
        if not report["ok"] or report["native_attempts"] or report["delegated_calls"]:
            raise ValueError("native fault nested source is invalid or used")
        delegated.update(
            {(None, "exec", "custom_tool_call"), ("functions", "exec", "custom_tool_call")}
        )
    return delegated


def audit_provider_interruption(proof):
    """Verify a cancelled forwarding boundary without promoting uncertain execution."""
    from aisle.harness.frontend_effects import _snapshot_effect, command_probe
    from aisle.harness.provider_response_authority import _response
    from aisle.harness.provider_source_audit import verify_provider_interruption_sources

    try:
        original = verify_original_launch(proof)
        scenario = read_bound_scenario(proof)
        artifacts, record = proof["artifacts"], proof["record"]
        arm = record["arm"]
        candidate = proof["admission"]["arms"][arm]
        launch = proof["admission"]["launch_bindings"][arm]
        profile = _json(artifacts["conformance/profile.json"])
        if "fixtures/fault.json" not in profile["fixture_files"]:
            raise ValueError("interruption fault was not bound before launch")
        fault = _json(artifacts["conformance/inputs/fixtures/fault.json"])
        kind, target, marker = fault["fault"], fault["target"], fault["marker"]
        command = command_probe(target, marker)
        if (
            kind not in {"cancel_before_forward", "cancel_after_forward"}
            or fault
            != {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": kind,
                "route": "native",
                "attempt": 2,
                "target": target,
                "marker": marker,
            }
            or target not in candidate["repository"]["editable_allowlist"]
            or scenario
            != {
                "schema_version": "aisle.frontend-conformance-scenario.v1",
                "route": "harness",
                "arm": arm,
                "case": "available",
                "operation": "check",
                "selector": {"attempt": 1},
            }
            or record["ok"] is not False
            or record["eligible_for_estimate"] is not False
        ):
            raise ValueError("interruption intent or failed session differs")
        forwarded = kind == "cancel_after_forward"
        authority = _authority_evidence(artifacts, unused_host=True)
        delegated = _native_fault_delegations(candidate, launch, authority)
        provider, dispatch = authority["provider"], authority["dispatch"]
        if _json(provider["artifacts"]["invocation.json"])["binding"] != launch["provider"]:
            raise ValueError("interrupted provider differs from admission")
        upstream = verify_provider_interruption_sources(
            **provider,
            dispatch=dispatch,
            delegated_tools=delegated,
            failed_attempt=2,
            forwarded=forwarded,
        )
        if (
            not upstream["ok"]
            or upstream["native_attempts"] != [2]
            or upstream["interrupted_attempts"] != [2]
            or dispatch["expected"]["attempts"] != 2
            or dispatch["expected"]["reserved"] != 2
        ):
            raise ValueError("interrupted delivery lacks its exact charged boundary")
        prefix, execution = _audit_native_fault_prefix(proof, authority, upstream)
        _, events, calls = _response(provider["artifacts"]["00000002-response.sse"])
        if len(calls) != 1:
            raise ValueError("interrupted response lacks one command")
        position, call = next(iter(calls.items()))
        if call[1:] != (None, "exec_command", "function_call") or _json(
            events[position][1]["item"]["arguments"]
        ) != {"cmd": command, "login": False}:
            raise ValueError("interrupted command differs from bound probe")
        owner = provider_call_owner(artifacts, attempt=2)
        for row, _ in prefix["sequences"]["received"]:
            params = row.get("params", {})
            item = params.get("item", {})
            if item.get("type") == "commandExecution":
                if (
                    not forwarded
                    or item.get("id") != call[0]
                    or params.get("threadId") != owner["thread_id"]
                    or params.get("turnId") != owner["turn_id"]
                ):
                    raise ValueError("unexpected execution across interrupted forwarding boundary")
        snapshots = record["snapshots"]
        unchanged = artifacts["authored/" + target] == artifacts["final/" + target]
        if (
            not forwarded
            and not unchanged
            or any(
                value != snapshots["final"].get(name)
                for name, value in snapshots["authored"].items()
                if name != target
            )
            or set(snapshots["authored"]) != set(snapshots["final"])
        ):
            raise ValueError("interruption changed an unforwarded or unrelated deliverable")
        effect = _snapshot_effect(proof, target, marker, unchanged)
        return {
            "fault_verified": True,
            "fault": kind,
            "charged_attempts": [1, 2],
            "delivery_uncertain": True,
            "executable_frame_forwarded": forwarded,
            "effect": effect,
            "native_execution_verified": False,
            "launch": original,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid provider interruption evidence") from exc


def _audit_native_fault_prefix(proof, authority, upstream):
    """Verify the successful harness prefix shared by native replay and interruption cases."""
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix

    artifacts, dispatch = proof["artifacts"], authority["dispatch"]
    prefix = read_protocol_prefix(**authority["protocol"])
    execution = _audit_execution(proof, complete_frontend=False, unused_host=True)
    if len(prefix["calls"]) != 1 or prefix["mcp_sources"]:
        raise ValueError("native fault requires one owned harness call")
    if match_provider_harness_calls(upstream["delegated_calls"], prefix):
        raise ValueError("unmatched native fault provider delegation")
    _, call, raw = prefix["calls"][0]
    reservation = _json(dispatch["artifacts"]["00000001-reservation.json"])
    if (
        reservation["decision"] != "authorized"
        or reservation["call"] != call
        or dispatch["artifacts"]["00000001.frame"] != raw
    ):
        raise ValueError("native fault harness call lacks exact reservation")
    attempts = [
        _json(raw)["record"]
        for raw in artifacts["tool-events.jsonl"].splitlines()
        if "record" in _json(raw)
    ]
    if (
        len(attempts) != 1
        or attempts[0]["operation"] != "check"
        or attempts[0]["ok"] is not True
        or attempts[0]["classification"] != "tool_result"
        or match_controller_replies(artifacts, prefix) != 1
    ):
        raise ValueError("native fault lacks a successful controller check and reply")
    return prefix, execution


def audit_provider_replay(proof):
    """Join a bound rejected replay to the successful owned execution prefix."""
    from aisle.harness.frontend_effects import command_probe
    from aisle.harness.provider_response_authority import _response
    from aisle.harness.provider_source_audit import verify_provider_replay_sources

    try:
        original = verify_original_launch(proof)
        scenario = read_bound_scenario(proof)
        artifacts, record = proof["artifacts"], proof["record"]
        arm = record["arm"]
        candidate = proof["admission"]["arms"][arm]
        launch = proof["admission"]["launch_bindings"][arm]
        profile = _json(artifacts["conformance/profile.json"])
        if "fixtures/fault.json" not in profile["fixture_files"]:
            raise ValueError("replay fault was not bound before launch")
        fault = _json(artifacts["conformance/inputs/fixtures/fault.json"])
        target, marker = fault["target"], fault["marker"]
        command = command_probe(target, marker)
        if (
            fault
            != {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": "provider_replay",
                "route": "native",
                "earlier_request_id": "00000002",
                "request_id": "00000003",
                "target": target,
                "marker": marker,
            }
            or target not in candidate["repository"]["editable_allowlist"]
            or scenario
            != {
                "schema_version": "aisle.frontend-conformance-scenario.v1",
                "route": "harness",
                "arm": arm,
                "case": "available",
                "operation": "check",
                "selector": {"attempt": 1},
            }
            or record["ok"] is not False
            or record["eligible_for_estimate"] is not False
            or record["snapshots"]["authored"] != record["snapshots"]["final"]
        ):
            raise ValueError("replay intent, failed session, or unchanged effect differs")
        authority = _authority_evidence(artifacts, unused_host=True)
        delegated = _native_fault_delegations(candidate, launch, authority)
        provider, dispatch = authority["provider"], authority["dispatch"]
        if _json(provider["artifacts"]["invocation.json"])["binding"] != launch["provider"]:
            raise ValueError("replay provider differs from admission")
        upstream = verify_provider_replay_sources(
            **provider, dispatch=dispatch, delegated_tools=delegated
        )
        if (
            not upstream["ok"]
            or upstream["native_attempts"] != [2]
            or upstream["replayed_requests"] != [fault["request_id"]]
            or dispatch["expected"]["attempts"] != 2
            or dispatch["expected"]["reserved"] != 2
        ):
            raise ValueError("replay lacks exact successful prefix and undispatched rejection")
        prefix, execution = _audit_native_fault_prefix(proof, authority, upstream)
        earlier_id, earlier_events, earlier_calls = _response(
            provider["artifacts"]["00000002-response.sse"]
        )
        rejected_id, rejected_events, rejected_calls = _response(
            provider["artifacts"]["00000003-response.sse"]
        )
        if len(earlier_calls) != 1 or len(rejected_calls) != 1 or earlier_id == rejected_id:
            raise ValueError("replay fixture does not isolate a reused call identity")
        earlier_position, earlier_call = next(iter(earlier_calls.items()))
        rejected_position, rejected_call = next(iter(rejected_calls.items()))
        if (
            earlier_call != rejected_call
            or earlier_call[1:] != (None, "exec_command", "function_call")
            or _json(earlier_events[earlier_position][1]["item"]["arguments"])
            != {"cmd": ":", "login": False}
            or _json(rejected_events[rejected_position][1]["item"]["arguments"])
            != {"cmd": command, "login": False}
        ):
            raise ValueError("replayed identity or rejected side-effect command differs")
        owner = provider_call_owner(artifacts, attempt=2)
        completed = [
            row["params"]
            for row, _ in prefix["sequences"]["received"]
            if row.get("method") == "item/completed"
            and row.get("params", {}).get("item", {}).get("type") == "commandExecution"
        ]
        if (
            len(completed) != 1
            or completed[0]["item"]["id"] != earlier_call[0]
            or completed[0]["threadId"] != owner["thread_id"]
            or completed[0]["turnId"] != owner["turn_id"]
            or completed[0]["item"]["status"] != "completed"
            or type(completed[0]["item"]["exitCode"]) is not int
            or completed[0]["item"]["exitCode"] != 0
        ):
            raise ValueError("replay lacks exactly one earlier successful native completion")
        return {
            "fault_verified": True,
            "fault": "provider_replay",
            "effect": "unchanged",
            "controller_attempts": 1,
            "launch": original,
            "execution": execution,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid provider replay evidence") from exc


def audit_hook_independence(proof):
    """Verify quota refusal under bound absent/changed hook configuration.

    This measures the provider control path; it does not claim that a configured
    hook actually executed or that hooks themselves provide authorization.
    """
    try:
        original = verify_original_launch(proof)
        case = route_scenario_evidence(proof)
        artifacts, record = proof["artifacts"], proof["record"]
        arm = record["arm"]
        ceiling = proof["admission"]["arms"][arm]["budget"]["frontend_tool_ceiling"]
        if type(ceiling) is not int or ceiling < 2:
            raise ValueError("invalid hook probe quota")
        refused_attempt = ceiling + 1
        profile = _json(artifacts["conformance/profile.json"])
        if "fixtures/fault.json" not in profile["fixture_files"]:
            raise ValueError("hook fault was not bound before launch")
        fault = _json(artifacts["conformance/inputs/fixtures/fault.json"])
        kind = fault.get("fault")
        if (
            kind not in {"hook_absent", "hook_changed"}
            or fault
            != {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": kind,
                "route": "native",
                "attempt": refused_attempt,
            }
            or case["route"] != "native"
            or case["case"] != "quota_refused"
            or case["selector"] != {"attempt": refused_attempt}
            or case["effect"]["effect"] != "unchanged"
        ):
            raise ValueError("hook fault does not establish the bound excess-call refusal")
        from aisle.harness.frontend_conformance import fault_launch

        binding = proof["admission"]["launch_bindings"][arm]
        argv = binding["argv"]
        nominal = {
            **binding,
            "argv": argv[:-2] if kind == "hook_absent" else [argv[0], *argv[2:-4]],
        }
        if argv != fault_launch(nominal, kind)["argv"]:
            raise ValueError("hook configuration differs from the fault case")
        reservation = _json(artifacts["frontend-dispatch/00000002-reservation.json"])
        from aisle.harness.frontend_app_server_audit import read_protocol_prefix

        prefix = read_protocol_prefix(**_authority_evidence(artifacts, refusal=True)["protocol"])
        owner = provider_call_owner(artifacts, attempt=2)
        calls = [
            row["params"]
            for row, _ in prefix["sequences"]["received"]
            if row.get("method") == "item/completed"
            and row.get("params", {}).get("item", {}).get("type") == "commandExecution"
        ]
        if reservation["decision"] != "authorized" or not any(
            row["item"]["id"] == reservation["call"]["call_id"]
            and row["threadId"] == owner["thread_id"]
            and row["turnId"] == owner["turn_id"]
            and row["item"]["status"] == "completed"
            and type(row["item"]["exitCode"]) is int
            and row["item"]["exitCode"] == 0
            for row in calls
        ):
            raise ValueError("hook case lacks an earlier completed native call")
        return {
            "fault_verified": True,
            "fault": kind,
            "effect": "unchanged",
            "launch": original,
            "hook_execution_verified": False,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid hook independence evidence") from exc


def audit_controller_denial(proof):
    """Replay a real negative controller verdict through its owned frontend reply."""
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix
    from aisle.harness.frontend_effects import _same_json
    from aisle.harness.matched_app_server import controller_reply

    try:
        original = verify_original_launch(proof)
        scenario = route_scenario_evidence(proof)
        record, artifacts = proof["record"], proof["artifacts"]
        arm = record["arm"]
        profile = _json(artifacts["conformance/profile.json"])
        if "fixtures/fault.json" not in profile["fixture_files"]:
            raise ValueError("denial was not a bound fixture input")
        fault = _json(artifacts["conformance/inputs/fixtures/fault.json"])
        target = "graphs/expert_t1.yaml" if arm == "typed" else "experts/monolithic/expert_t1.py"
        invalid = b"nodes: [broken YAML" if arm == "typed" else b"invalid python : :"
        if (
            fault
            != {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": "denial",
                "route": "harness",
                "operation": "check",
                "attempt": 1,
                "target": target,
                "sha256": hashlib.sha256(invalid).hexdigest(),
            }
            or artifacts["authored/" + target] != invalid
            or record["snapshots"]["authored"] != record["snapshots"]["final"]
            or scenario["route"] != "harness"
            or scenario["case"] != "available"
            or scenario["selector"] != {"attempt": 1}
        ):
            raise ValueError("denial source differs from bound invalid input")
        attempts = [
            _json(raw)["record"]
            for raw in artifacts["tool-events.jsonl"].splitlines()
            if "record" in _json(raw)
        ]
        links = [_json(raw) for raw in artifacts["tool-request-index.jsonl"].splitlines()]
        if len(attempts) != 1 or len(links) != 1:
            raise ValueError("denial requires one controller attempt and service request")
        attempt, link = attempts[0], links[0]
        if (
            attempt["operation"] != "check"
            or attempt["attempt"] != 1
            or attempt["classification"] != "tool_result"
            or attempt["ok"] is not False
            or attempt["result"]["ok"] is not False
            or attempt["process"]["rc"] != 1
            or link["attempt_id"] != attempt["immutable_id"]
        ):
            raise ValueError("denial is not a real negative controller verdict")
        authority = _authority_evidence(artifacts)
        prefix = read_protocol_prefix(**authority["protocol"])
        if len(prefix["calls"]) != 1 or not prefix["scope"].complete:
            raise ValueError("denial lacks a completed owned frontend exchange")
        wire, call, _ = prefix["calls"][0]
        if call != link["frontend_authorization"]["call"]:
            raise ValueError("denial frontend call differs from its service grant")
        replies = [
            row["result"]
            for row in prefix["sent"][4:]
            if type(row.get("id")) is type(wire) and row.get("id") == wire
        ]
        response = {
            key: attempt[key] for key in ("ok", "classification", "result", "error", "attempt")
        }
        response["request_id"] = link["request_id"]
        expected = controller_reply(response, request_id=link["request_id"])
        if len(replies) != 1 or not _same_json(replies[0], expected):
            raise ValueError("denial reply promoted or changed the controller result")
        return {
            "fault_verified": True,
            "fault": "denial",
            "controller_attempts": 1,
            "frontend_success": False,
            "launch": original,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid controller denial evidence") from exc


def audit_controller_unavailable(proof):
    """Verify the explicitly bound controller-unavailable case."""
    result = audit_controller_fault(proof)
    if result["fault"] != "controller_unavailable":
        raise ValueError("not a controller-unavailable case")
    return result


def audit_controller_fault(proof):
    """Verify an injected first-call outage without treating uncertain delivery as success.

    The caller acquires the original session with read_session_proof. This proves
    the bounded fault case, not general failure coverage or external confinement.
    """
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal
    from aisle.harness.provider_source_audit import verify_provider_failure_prefix

    try:
        scenario = read_bound_scenario(proof)
        original = verify_original_launch(proof)
        record, admission, artifacts = proof["record"], proof["admission"], proof["artifacts"]
        arm, session = record["arm"], record["session_id"]
        launch, candidate = admission["launch_bindings"][arm], admission["arms"][arm]
        profile = _json(artifacts["conformance/profile.json"])
        fault_name = "fixtures/fault.json"
        if fault_name not in profile["fixture_files"]:
            raise ValueError("outage was not a bound fixture input")
        fault = _json(artifacts["conformance/inputs/" + fault_name])
        kind = fault.get("fault")
        if kind not in {"controller_unavailable", "controller_timeout", "malformed_response"}:
            raise ValueError("unsupported controller fault")
        error_type = "TimeoutError" if kind == "controller_timeout" else "ValueError"
        service_error = (
            "injected controller unavailable" if kind == "controller_unavailable" else None
        )
        if (
            fault
            != {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": kind,
                "route": "harness",
                "operation": "check",
                "attempt": 1,
            }
            or scenario["route"] != "harness"
            or scenario["operation"] != "check"
            or scenario["selector"] != {"attempt": 1}
            or record["ok"] is not False
            or record["eligible_for_estimate"] is not False
            or record["plan_id"] != admission["immutable_id"]
            or record["snapshots"]["authored"] != record["snapshots"]["final"]
            or artifacts["tool-events.jsonl"] != b""
            or artifacts.get("tool-request-index.jsonl", b"") != b""
            or any(name.startswith(("request-", "attempt-")) for name in artifacts)
        ):
            raise ValueError("outage case has execution, changed effects, or different intent")
        failures = (
            [
                {"error_type": "TimeoutError", "error": ""},
                {
                    "error_type": "TimeoutError",
                    "error": "authorized tool response deadline expired",
                },
            ]
            if kind == "controller_timeout"
            else [{"error_type": "ValueError", "error": "invalid controller response fields"}]
            if kind == "malformed_response"
            else [
                {
                    "error_type": "ValueError",
                    "error": "authorized tool service failed: injected controller unavailable",
                }
            ]
        )
        failure = _json(artifacts["frontend-protocol-reference.json"]).get("failure")
        if failure not in failures:
            raise ValueError("outage lacks its owned terminal frontend request")
        authority = _authority_evidence(
            artifacts,
            unused_host=True,
            expected_host_failure=failure["error_type"] + ": " + failure["error"],
        )
        delegated = _native_fault_delegations(candidate, launch, authority)
        for directory, source in (
            ("frontend-authority", authority),
            ("frontend-dispatch", authority["dispatch"]),
            ("provider", authority["provider"]),
        ):
            if {
                name.removeprefix(directory + "/")
                for name in artifacts
                if name.startswith(directory + "/")
            } != set(source["artifacts"]):
                raise ValueError("unindexed outage source")
        dispatch = authority["dispatch"]
        replay = verify_dispatch_journal(**dispatch)
        if (
            not replay["ok"]
            or replay["delivery_uncertain"] is not True
            or replay["attempts"] != 1
            or replay["reserved"] != 1
            or dispatch["expected"]["session_id"] != session
            or dispatch["expected"]["ceiling"] != candidate["budget"]["frontend_tool_ceiling"]
            or authority["expected"]["session_id"] != session
        ):
            raise ValueError("outage does not preserve one charged uncertain delivery")
        prefix = read_protocol_prefix(**authority["protocol"])
        if (
            authority["protocol"]["expected"].get("failure") not in failures
            or len(prefix["calls"]) != 1
            or prefix["scope"].complete
            or prefix["mcp_sources"]
        ):
            raise ValueError("outage lacks its owned terminal frontend request")
        wire, call, raw = prefix["calls"][0]
        reservation = _json(dispatch["artifacts"]["00000001-reservation.json"])
        delivery = _json(dispatch["artifacts"]["00000001-delivery.json"])
        if (
            reservation["call"] != call
            or call["tool_name"] != "harness.check"
            or raw != dispatch["artifacts"]["00000001.frame"]
            or prefix["sequences"]["received"][-1][1] != raw
            or delivery["status"] != "uncertain"
            or delivery["error_type"] != error_type
            or any(
                type(row.get("id")) is type(wire) and row.get("id") == wire
                for row in prefix["sent"][4:]
            )
        ):
            raise ValueError("outage request differs or received a reply")
        grants = authority["artifacts"]
        names = [name for name in grants if re.fullmatch(r"[a-f0-9]{64}-grant.json", name)]
        if len(names) != 1:
            raise ValueError("outage grant was consumed, missing, or duplicated")
        grant = _json(grants[names[0]])
        request_id = grant["request_id"]
        if type(request_id) is not str or re.fullmatch(r"[a-f0-9]{32}", request_id) is None:
            raise ValueError("invalid outage request identity")
        expected_files = {"authority.json", names[0]}
        if kind == "malformed_response":
            expected_files.add(request_id + "-response-rejected.json")
        if set(grants) != expected_files:
            raise ValueError("outage grant was consumed or rejection evidence differs")
        request = (
            json.dumps(
                {
                    "schema_version": "aisle.matched-tool-request.v1",
                    "id": request_id,
                    "operation": "check",
                }
            ).encode()
            + b"\n"
        )
        expected_grant = {
            "schema_version": "aisle.frontend-request-grant.v1",
            "authorization_id": names[0].removesuffix("-grant.json"),
            "session_id": session,
            "call": call,
            "request_id": request_id,
            "request_sha256": hashlib.sha256(request).hexdigest(),
            "request_bytes": len(request),
        }
        if grant != expected_grant or _json(grants["authority.json"]) != {
            "schema_version": "aisle.frontend-request-authority.v1",
            "session_id": session,
            "complete_coverage": False,
            "confinement_verified": False,
        }:
            raise ValueError("outage grant differs from its owned request")
        service = _json(artifacts["tool-service.json"])
        if service != {
            "schema_version": "aisle.matched-tool-service.v1",
            "session_id": session,
            "plan_id": record["plan_id"],
            "arm": arm,
            "ok": False,
            "request_authority_required": True,
            "processed_requests": 0,
            "seen_requests": 0,
            "pending_requests": [request_id + ".request.json"],
            "error": service_error,
        }:
            raise ValueError("outage service did not retain the unused request")
        if kind == "malformed_response":
            from aisle.harness.matched_app_server import controller_reply

            rejected = _json(grants[request_id + "-response-rejected.json"])
            response = {
                "request_id": request_id,
                "ok": "false",
                "classification": "tool_result",
                "result": {"ok": False},
                "error": None,
                "attempt": 1,
            }
            if rejected != {
                "schema_version": "aisle.frontend-response-rejection.v1",
                "session_id": session,
                "request_id": request_id,
                "call": call,
                "response": response,
            }:
                raise ValueError(
                    "malformed response differs from the bound fault and owned request"
                )
            try:
                controller_reply(rejected["response"], request_id=request_id)
            except ValueError as exc:
                if str(exc) != authority["protocol"]["expected"]["failure"]["error"]:
                    raise ValueError("malformed response rejection cause differs") from exc
            else:
                raise ValueError("retained malformed response was actually valid")
        provider = authority["provider"]
        if _json(provider["artifacts"]["invocation.json"])["binding"] != launch["provider"]:
            raise ValueError("outage provider differs from admission")
        upstream = verify_provider_failure_prefix(
            **provider, dispatch=dispatch, delegated_tools=delegated, failed_attempt=1
        )
        if (
            not upstream["ok"]
            or upstream["native_attempts"]
            or upstream.get("hosted_calls")
            or match_provider_harness_calls(upstream["delegated_calls"], prefix)
        ):
            raise ValueError("outage provider prefix has unmatched or executed work")
        return {
            "fault_verified": True,
            "fault": kind,
            "controller_attempts": 0,
            "dispatch_delivery_uncertain": True,
            "launch": original,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid controller outage evidence") from exc


def audit_refusal_execution(proof):
    """Verify the executed harness prefix and prove no refused call obtained a grant.

    This returns separate source and execution evidence, not effect or complete
    conformance qualification. Native/provider/MCP lifecycle checks still belong
    to their respective source audits and full scenario acceptance.
    """
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix

    sources = refusal_source_evidence(proof)
    execution = _audit_execution(proof, complete_frontend=False)
    try:
        artifacts = proof["artifacts"]
        authority = _authority_evidence(artifacts, refusal=True)
        prefix = read_protocol_prefix(**authority["protocol"])
        dispatch = authority["dispatch"]
        calls = [(call, raw) for _, call, raw in prefix["calls"]]
        for raw in prefix["mcp_sources"].values():
            params = _json(raw)["params"]
            calls.append(
                (
                    {
                        "turn_id": params["turnId"],
                        "call_id": params["item"]["id"],
                        "tool_name": "harness." + params["item"]["tool"],
                    },
                    raw,
                )
            )
        reservations = []
        for number in range(1, dispatch["expected"]["attempts"] + 1):
            base = f"{number:08d}"
            reservations.append(
                (
                    number,
                    _json(dispatch["artifacts"][base + "-reservation.json"]),
                    dispatch["artifacts"][base + ".frame"],
                )
            )
        links = [_json(raw) for raw in artifacts.get("tool-request-index.jsonl", b"").splitlines()]
        used = set()
        for link in links:
            grant = link["frontend_authorization"]
            matches = [raw for call, raw in calls if call == grant["call"]]
            if len(matches) != 1:
                raise ValueError("consumed grant lacks a unique owned frontend call")
            slots = [
                number
                for number, row, raw in reservations
                if row["call"] == grant["call"]
                and raw == matches[0]
                and row["decision"] == "authorized"
            ]
            if len(slots) != 1 or slots[0] in used or slots[0] in sources["linked_local_attempts"]:
                raise ValueError("consumed grant lacks unique pre-execution authorization")
            used.add(slots[0])
        # Every reserved harness call must have reached the audited service chain.
        expected = {
            number
            for number, row, _ in reservations
            if row["decision"] == "authorized" and row["call"]["tool_name"].startswith("harness.")
        }
        if used != expected:
            raise ValueError("authorized harness prefix differs from consumed grants")
        completed_calls = {
            (row["call"]["turn_id"], row["call"]["call_id"])
            for number, row, _ in reservations
            if number in used
        }
        controller_replies = match_controller_replies(
            artifacts,
            {
                **prefix,
                "calls": [
                    (wire, call, raw)
                    for wire, call, raw in prefix["calls"]
                    if (call["turn_id"], call["call_id"]) in completed_calls
                ],
            },
        )
        nested_links = []
        if "code-mode-reference.json" in artifacts:
            nested_links = _nested_harness_evidence(
                proof,
                prefix=prefix,
                nested_report=sources["source_audits"].get("nested"),
                refusal=True,
            )
            provider_prefix = sources["source_audits"].get(
                "provider_prefix",
                sources["source_audits"].get("provider", sources["source_audits"].get("hosted")),
            )
            if provider_prefix is not None:
                unresolved = provider_prefix["unlinked_nested_harness_calls"]
                if len(unresolved) != len(nested_links) or {
                    row["call_id"] for row in unresolved
                } != {row["call_id"] for row in nested_links}:
                    raise ValueError("nested completion links differ from delegated harness calls")
                provider_prefix["unlinked_nested_harness_calls"] = []
        return {
            "source_audit": sources,
            "execution_audit": execution,
            "controller_replies": controller_replies,
            "nested_harness_links": nested_links,
            "authorized_harness_attempts": sorted(used),
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid refusal execution prefix") from exc
