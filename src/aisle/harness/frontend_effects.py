"""Deterministic conformance effects over controller-retained editable snapshots.

These checks complement source/execution audits; they never establish route
coverage or confinement on their own. No live filesystem reads are performed.
"""

from __future__ import annotations

import hashlib
import json
import math
import shlex

from aisle.harness.frontend_conformance import MAX_INPUT_BYTES
from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal
from aisle.harness.frontend_qualification import _json, _name


def nested_observation_ms(wall_ceiling_s):
    """Convert the admitted tool deadline to the host's uint64 observation field."""
    if type(wall_ceiling_s) not in (int, float) or not 0 < wall_ceiling_s < (2**64 - 1) / 1000:
        raise ValueError("invalid nested observation deadline")
    return math.ceil(wall_ceiling_s * 1000)


def nested_execution(script, *, wall_ceiling_s):
    """Observe the nested cell for its admitted tool deadline; grant no extra budget."""
    milliseconds = nested_observation_ms(wall_ceiling_s)
    return "// @exec: " + json.dumps({"yield_time_ms": milliseconds}) + "\n" + script


def _same_json(left, right):
    """Preserve JSON scalar types (False is not 0 and a process ID is not a float)."""
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def command_probe(target, marker):
    """Render one fixed append probe; arbitrary shell programs are not interpreted."""
    _name(target)
    if (
        type(marker) is not str
        or not marker.startswith("# AISLE probe ")
        or len(marker) > 256
        or any(ord(c) < 32 for c in marker)
    ):
        raise ValueError("invalid command probe marker")
    return "printf '%s\\n' " + shlex.quote(marker) + " >> " + shlex.quote(target)


def command_effect_evidence(proof, *, attempt, target, marker, refused):
    """Verify the reserved command and exact append/no-change outcome of its target.

    Caller must additionally verify the selected attempt's owned source and
    execution/refusal path. This function does not promote unchanged bytes to
    quota proof and does not add tool/controller counts.
    """
    return _effect_evidence(
        proof, attempt=attempt, target=target, marker=marker, refused=refused, kind="command"
    )


def edit_probe(target, marker, before):
    """Render a whole-file context patch that appends one marker at end of file."""
    command_probe(target, marker)
    if target != target.strip() or any(ord(c) < 32 for c in target):
        raise ValueError("invalid edit probe target")
    if (
        type(before) is not bytes
        or not before
        or len(before) > MAX_INPUT_BYTES
        or not before.endswith(b"\n")
    ):
        raise ValueError("edit probe requires a bounded newline-terminated baseline")
    lines = before.decode().split("\n")[:-1]
    if any("\r" in line for line in lines):
        raise ValueError("unsupported edit probe line endings")
    return (
        "*** Begin Patch\n*** Update File: "
        + target
        + "\n@@\n"
        + "".join(" " + line + "\n" for line in lines)
        + "+"
        + marker
        + "\n*** End of File\n*** End Patch\n"
    )


def edit_effect_evidence(proof, *, attempt, target, marker, refused):
    """Check an exact reserved native patch and the resulting editable snapshot."""
    return _effect_evidence(
        proof, attempt=attempt, target=target, marker=marker, refused=refused, kind="edit"
    )


def continued_probe(target):
    """Append exactly one line received through a retained process interaction."""
    _name(target)
    return "IFS= read -r aisle_probe && printf '%s\\n' \"$aisle_probe\" >> " + shlex.quote(target)


def continued_effect_evidence(proof, *, attempt, startup_attempt, target, marker, refused):
    """Bind continued input to its startup reservation and owned terminal interaction."""
    return _effect_evidence(
        proof,
        attempt=attempt,
        target=target,
        marker=marker,
        refused=refused,
        kind="continued",
        startup_attempt=startup_attempt,
    )


def mcp_effect_evidence(proof, *, attempt, target, marker, refused):
    """Check the bound fixture's explicit MCP append operation, not arbitrary MCP semantics."""
    return _effect_evidence(
        proof, attempt=attempt, target=target, marker=marker, refused=refused, kind="mcp"
    )


def _effect_evidence(proof, *, attempt, target, marker, refused, kind, startup_attempt=None):
    try:
        command = command_probe(target, marker)
        if type(attempt) is not int or attempt <= 0 or type(refused) is not bool:
            raise ValueError("invalid effect decision")
        artifacts, record = proof["artifacts"], proof["record"]
        manifest = proof["admission"]["arms"][record["arm"]]
        if target not in manifest["repository"]["editable_allowlist"]:
            raise ValueError("effect target is not an admitted editable deliverable")
        reference = _json(artifacts["frontend-dispatch-reference.json"])
        dispatch = {name: artifacts["frontend-dispatch/" + name] for name in reference["artifacts"]}
        replay = verify_dispatch_journal(dispatch, expected=reference, byte_limit=MAX_INPUT_BYTES)
        if not replay["ok"] or replay["delivery_uncertain"] or attempt > reference["attempts"]:
            raise ValueError("effect requires a valid reserved attempt")
        base = f"{attempt:08d}"
        reservation = _json(dispatch[base + "-reservation.json"])
        if reservation["decision"] != ("refused" if refused else "authorized") or (
            refused and reservation["reason"] != "dispatch budget exhausted"
        ):
            raise ValueError("effect decision differs from reservation")
        call, raw = reservation["call"], dispatch[base + ".frame"]
        if call["turn_id"].startswith("code-mode:"):
            if kind != "command":
                raise ValueError("unsupported nested edit effect")
            from aisle.harness.code_mode_authority import _decode

            message = _decode("ToolCall", raw)
            if (
                call["tool_name"] != "exec_command"
                or message.tool_name.name != "exec_command"
                or message.tool_name.namespace
            ):
                raise ValueError("effect is not a nested command")
            arguments = _json(message.input_json)
        else:
            from aisle.harness.provider_response_authority import _events

            events = list(_events(raw))
            if len(events) != 1 or events[0][1] is None:
                raise ValueError("effect lacks a unique provider call")
            item = events[0][1]["item"]
            tool, wire_kind = {
                "command": ("exec_command", "function_call"),
                "edit": ("apply_patch", "custom_tool_call"),
                "mcp": ("append", "function_call"),
                "continued": ("write_stdin", "function_call"),
            }[kind]
            if (
                item["type"] != wire_kind
                or item["name"] != tool
                or item["call_id"] != call["call_id"]
                or _json(call["tool_name"].encode()) != [item.get("namespace"), tool, wire_kind]
            ):
                raise ValueError("effect source is not the reserved command")
            if kind == "mcp" and item.get("namespace") != "mcp__aisle_fixture":
                raise ValueError("effect does not name the bound MCP fixture")
            arguments = _json(item["arguments"].encode()) if kind != "edit" else item["input"]
        requested = (
            {"cmd": command, "login": False}
            if kind == "command"
            else {"target": target, "marker": marker}
            if kind == "mcp"
            else edit_probe(target, marker, artifacts["authored/" + target])
            if kind == "edit"
            else _continued_arguments(
                artifacts, dispatch, attempt, startup_attempt, target, marker, refused
            )
        )
        if not _same_json(arguments, requested):
            raise ValueError("command does not request the bound deterministic effect")
        return {"attempt": attempt, **_snapshot_effect(proof, target, marker, refused)}
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid command effect evidence") from exc


def _continued_arguments(artifacts, dispatch, attempt, startup_attempt, target, marker, refused):
    from aisle.harness.frontend_app_server import AppServerScope
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix
    from aisle.harness.provider_response_authority import _events

    if type(startup_attempt) is not int or not 0 < startup_attempt < attempt:
        raise ValueError("continued input requires a prior startup attempt")
    base = f"{startup_attempt:08d}"
    startup = _json(dispatch[base + "-reservation.json"])
    events = list(_events(dispatch[base + ".frame"]))
    if startup["decision"] != "authorized" or len(events) != 1 or events[0][1] is None:
        raise ValueError("continued startup was not authorized")
    item = events[0][1]["item"]
    if (
        item["type"] != "function_call"
        or item["name"] != "exec_command"
        or item["call_id"] != startup["call"]["call_id"]
        or _json(startup["call"]["tool_name"].encode())
        != [item.get("namespace"), "exec_command", "function_call"]
        or not _same_json(
            _json(item["arguments"].encode()),
            {"cmd": continued_probe(target), "login": False, "tty": True},
        )
    ):
        raise ValueError("continued startup does not request the bound probe")
    reference = _json(artifacts["frontend-protocol-reference.json"])
    protocol = {n: artifacts["frontend-protocol/" + n] for n in reference["artifacts"]}
    prefix = read_protocol_prefix(protocol, expected=reference, byte_limit=MAX_INPUT_BYTES)
    scope = AppServerScope(reference["thread_id"], reference["turn_id"])
    started, interactions = [], []
    for row, _ in prefix["sequences"]["received"]:
        params = row.get("params", {})
        observed = params.get("item", {})
        if (
            row.get("method") == "item/started"
            and observed.get("type") == "commandExecution"
            and observed.get("id") == item["call_id"]
        ):
            scope._identity(params.get("threadId"), params.get("turnId"))
            if observed.get("commandActions") != [
                {"type": "unknown", "command": continued_probe(target)}
            ]:
                raise ValueError("owned startup command differs")
            started.append((observed.get("processId"), params["threadId"], params["turnId"]))
        if (
            row.get("method") == "item/commandExecution/terminalInteraction"
            and params.get("itemId") == item["call_id"]
        ):
            scope._identity(params.get("threadId"), params.get("turnId"))
            interactions.append(
                (params.get("processId"), params["threadId"], params["turnId"], params.get("stdin"))
            )
        if row is not prefix["failed_turn"]:
            scope.feed(row)
    if (
        len(started) != 1
        or type(started[0][0]) is not str
        or not started[0][0].isascii()
        or not started[0][0].isdigit()
    ):
        raise ValueError("continued startup lacks a unique owned process")
    if interactions != ([] if refused else [(*started[0], marker + "\n")]):
        raise ValueError("continued input delivery differs from the quota decision")
    return {"session_id": int(started[0][0]), "chars": marker + "\n"}


def _snapshot_effect(proof, target, marker, refused):
    artifacts, record = proof["artifacts"], proof["record"]
    if target not in proof["admission"]["arms"][record["arm"]]["repository"]["editable_allowlist"]:
        raise ValueError("effect target is not an admitted editable deliverable")
    snapshots, contents = record["snapshots"], {}
    for phase in ("authored", "final"):
        data = artifacts[phase + "/" + target]
        mode = snapshots[phase][target]["mode"]
        if type(mode) is not int or not 0 <= mode <= 0o777:
            raise ValueError("invalid effect snapshot permissions")
        if (
            type(data) is not bytes
            or len(data) > MAX_INPUT_BYTES
            or hashlib.sha256(data).hexdigest() != snapshots[phase][target]["sha256"]
        ):
            raise ValueError("effect snapshot differs from controller receipt")
        contents[phase] = data
    if snapshots["authored"][target]["mode"] != snapshots["final"][target]["mode"]:
        raise ValueError("effect changed target permissions")
    expected = contents["authored"] + (b"" if refused else marker.encode() + b"\n")
    if contents["final"] != expected:
        raise ValueError("observed target bytes differ from the expected effect")
    return {
        "target": target,
        "effect": "unchanged" if refused else "appended",
        "complete_coverage": False,
        "confinement_verified": False,
    }


def concurrent_probe(ceiling, *, mcp_harness, target, marker):
    """Generate overlapping nested calls after consuming all but one reservation."""
    if type(ceiling) is not int or ceiling < 2 or type(mcp_harness) is not bool:
        raise ValueError("concurrent probe requires a bound quota and harness route")
    command = command_probe(target, marker)
    harness = "mcp__aisle_harness__check" if mcp_harness else "harness__check"
    prefix = "await tools." + harness + "({}); "
    prefix += 'await tools.exec_command({cmd:":",login:false}); ' * (ceiling - 2)
    return (
        prefix
        + "await Promise.allSettled(["
        + ",".join(
            "tools.exec_command(" + json.dumps(args) + ")"
            for args in ({"cmd": "sleep 1", "login": False}, {"cmd": command, "login": False})
        )
        + "]);"
    )


def hosted_probe(target, marker):
    """Encode the explicit intent that a bound hosted fixture executes as one search."""
    command_probe(target, marker)
    return "AISLE_HOSTED_PROBE " + json.dumps(
        {"target": target, "marker": marker}, sort_keys=True, separators=(",", ":")
    )


def hosted_probe_set(targets, marker):
    """Bind both representations in one shared instruction; execute only the active arm."""
    if type(targets) is not dict or set(targets) != {"typed", "monolithic"}:
        raise ValueError("hosted probe set requires both admitted arms")
    for target in targets.values():
        command_probe(target, marker)
    return "AISLE_HOSTED_PROBE_SET " + json.dumps(
        {"targets": targets, "marker": marker}, sort_keys=True, separators=(",", ":")
    )


def hosted_effect_evidence(proof, *, request_id, target, marker, refused):
    """Verify a local hosted-fixture effect; never attest a production provider."""
    from aisle.harness.provider_hosted import hosted_frontend_items
    from aisle.harness.provider_relay import verify_provider

    try:
        query = hosted_probe(target, marker)
        if type(request_id) is not str or not request_id or type(refused) is not bool:
            raise ValueError("invalid hosted effect identity")
        artifacts, record = proof["artifacts"], proof["record"]
        binding = proof["admission"]["launch_bindings"][record["arm"]]["provider"]
        verify_provider(binding)
        if binding.get("hosted_tool_contract") != "aisle.fixture.responses.max_tool_calls.v1":
            raise ValueError("filesystem hosted effects require the bound local fixture")
        reference = _json(artifacts["frontend-dispatch-reference.json"])
        dispatch = {n: artifacts["frontend-dispatch/" + n] for n in reference["artifacts"]}
        replay = verify_dispatch_journal(dispatch, expected=reference, byte_limit=MAX_INPUT_BYTES)
        if not replay["ok"] or replay["delivery_uncertain"]:
            raise ValueError("invalid hosted effect dispatch evidence")
        matches = []
        for i in range(1, reference.get("hosted_requests", 0) + 1):
            base = f"hosted-{i:08d}"
            if _json(dispatch[base + "-reservation.json"])["request_id"] == request_id:
                matches.append(base)
        if len(matches) != 1:
            raise ValueError("hosted effect lacks a unique request")
        base = matches[0]
        request = _json(dispatch[base + "-request.frame"])
        probes = []
        for message in request.get("input", []):
            if type(message) is not dict or message.get("role") not in {"developer", "user"}:
                continue
            content = message.get("content", [])
            if type(content) is str:
                content = [{"type": "input_text", "text": content}]
            for part in content:
                if (
                    type(part) is dict
                    and part.get("type") == "input_text"
                    and type(part.get("text")) is str
                ):
                    probes.extend(
                        line
                        for line in part["text"].splitlines()
                        if line.startswith(("AISLE_HOSTED_PROBE ", "AISLE_HOSTED_PROBE_SET "))
                    )
        if probes != [query]:
            targets = {
                arm: candidate["repository"]["editable_allowlist"][0]
                for arm, candidate in proof["admission"]["arms"].items()
            }
            if targets.get(record["arm"]) != target or probes != [
                hosted_probe_set(targets, marker)
            ]:
                raise ValueError("hosted request does not name the exact effect probe")
        settlement = _json(dispatch[base + "-settlement.json"])
        if settlement["status"] != ("refused" if refused else "completed"):
            raise ValueError("hosted effect disposition differs")
        if not refused:
            items = hosted_frontend_items(dispatch[base + "-response.frame"])
            if len(items) != 1 or items[0]["action"] != {
                "type": "search",
                "query": query,
                "queries": None,
            }:
                raise ValueError("hosted response executed another search effect")
        return {"request_id": request_id, **_snapshot_effect(proof, target, marker, refused)}
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid hosted effect evidence") from exc
