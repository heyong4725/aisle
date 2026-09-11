"""MON-8/MON-12/MON-13: actual frontend evidence through the admitted session launcher.

The adapter is explicitly synthetic. These receipts test dispatch qualification
composition; they do not attest OS confinement or authorize study collection.
"""

import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_frontend_continued_input import _frame

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

pytestmark = pytest.mark.accept


def _case_positions(route, *, unified, refused):
    """Select the effect only after the declared shared quota is consumed."""
    startup = (
        1
        if route == "harness"
        else 3
        if route in {"continued_input", "subagents", "harness_child"}
        else 2
    )
    ceiling = 4 if unified else startup
    return ceiling, ceiling + 1 if refused else startup


def _provider_replay_frame(index, *, target, marker):
    """Replay an already delivered no-op identity with a changed append request."""
    from aisle.harness.frontend_effects import command_probe

    if index not in (2, 3):
        raise ValueError("provider replay requires its original or replay request")
    frame = _frame(
        index,
        "exec_command",
        {"cmd": ":" if index == 2 else command_probe(target, marker), "login": False},
    )
    return frame.replace(b'"call_id": "call_3"', b'"call_id": "call_2"')


def _batch_frames(*frames):
    """Offer two ordered local calls in one provider response."""
    responses = [
        json.loads(line[6:])["response"]
        for frame in frames
        for line in frame.splitlines()
        if line.startswith(b"data: ") and json.loads(line[6:])["type"] == "response.completed"
    ]
    assert len(responses) == len(frames) == 2
    items = [item for response in responses for item in response["output"]]
    response = {**responses[0], "output": items}
    events = [
        {
            "type": "response.created",
            "response": {**response, "status": "in_progress", "output": []},
        },
        *[
            {"type": "response.output_item.done", "output_index": index, "item": item}
            for index, item in enumerate(items)
        ],
        {"type": "response.completed", "response": response},
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _child_start_frame(index, *, wall_ceiling_s):
    """Wait in the frontend, outside the provider's shared hosted allowance."""
    from aisle.harness.frontend_effects import nested_observation_ms

    spawn = _frame(
        index,
        "spawn_agent",
        {"task_name": "coverage_child", "message": "AISLE_CHILD_ROUTE", "fork_turns": "none"},
    ).replace(b'"name": "spawn_agent"', b'"namespace": "collaboration", "name": "spawn_agent"')
    wait = _frame(
        f"wait_{index}",
        "wait_agent",
        {"timeout_ms": nested_observation_ms(wall_ceiling_s)},
    ).replace(b'"name": "wait_agent"', b'"namespace": "collaboration", "name": "wait_agent"')
    return _batch_frames(spawn, wait)


def _effect_frame(index, route, *, target, marker, before, operation, body):
    """Render the same route-specific effect independently of provider turn count."""
    from aisle.harness.frontend_effects import command_probe, edit_probe

    if route in {"harness", "harness_child"}:
        name = operation if route == "harness" else "check"
        namespace = "harness" if route == "harness" else "mcp__aisle_harness"
        return _frame(index, name, {}).replace(
            ('"name": "' + name + '"').encode(),
            ('"namespace": "' + namespace + '", "name": "' + name + '"').encode(),
        )
    if route == "mcp":
        return _frame(index, "append", {"target": target, "marker": marker}).replace(
            b'"name": "append"', b'"namespace": "mcp__aisle_fixture", "name": "append"'
        )
    if route == "continued_input":
        reply = next(
            row["output"]
            for row in body["input"]
            if row.get("type") == "function_call_output" and row.get("call_id") == "call_2"
        )
        session = re.search(r"Process running with session ID (\d+)", reply)
        assert session is not None, reply
        return _frame(
            index, "write_stdin", {"session_id": int(session.group(1)), "chars": marker + "\n"}
        )
    frame = _frame(index, "exec_command", {"cmd": command_probe(target, marker), "login": False})
    if route == "native_edit":
        events = [json.loads(line[6:]) for line in frame.splitlines() if line.startswith(b"data: ")]
        for event in events:
            items = (
                [event["item"]] if "item" in event else event.get("response", {}).get("output", [])
            )
            for item in items:
                item.pop("arguments")
                item.update(
                    type="custom_tool_call",
                    name="apply_patch",
                    input=edit_probe(target, marker, before),
                )
        frame = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        ).encode()
    return frame


def _research_contract(candidates, *, route, unified, target, marker):
    """Keep common configuration fixed while naming each arm's hosted effect."""
    from aisle.harness.frontend_effects import hosted_probe, hosted_probe_set

    if unified:
        targets = {
            arm: candidate["repository"]["editable_allowlist"][0]
            for arm, candidate in candidates.items()
        }
        return (
            "research contract\nFor local hosted fixture probes, use only the active arm target.\n"
            + hosted_probe_set(targets, marker)
        )
    return "research contract" + ("\n" + hosted_probe(target, marker) if route == "hosted" else "")


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("refused", [False, True])
@pytest.mark.parametrize(
    "route",
    [
        "harness",
        "harness_child",
        "native",
        "native_edit",
        "continued_input",
        "nested",
        "mcp",
        "hosted",
        "subagents",
    ],
)
def test_admitted_actual_session_composes_source_execution_and_effect(
    tmp_path, arm, refused, route, *, fault=None, unified=False, shared=None, run_setup=None
):
    """MON-8/MON-12/MON-13: immutable session receipts prove allowed and refused effects."""
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if sys.platform != "darwin" or not binary:
        pytest.skip("requires explicitly selected actual Codex on macOS")
    host = os.environ.get("AISLE_CODE_MODE_HOST_BINARY") if route == "nested" or unified else None
    if (route == "nested" or unified) and not host:
        pytest.skip("requires explicitly selected actual Code Mode host")
    if run_setup is not None:
        from conformance_run_fixture import activate_prepared_controller

        activate_prepared_controller(Path(run_setup["base"]) / "controller")
    import frontend_codex_probe as probe
    from matched_campaign import run_engineering_session
    from test_frontend_nested_dispatch import _frame as nested_frame
    from test_matched_session import _access_log, _ambient_pair, _confinement_pair, prepared_pair
    from test_treatment_confinement import _attestation
    from test_typed_validation_binding import _binding as validation_binding

    from aisle.harness.frontend_conformance import (
        ROUTE_UNITS,
        acquire_session_inputs,
        configuration_digest,
        execution_digest,
        fault_launch,
        read_session_proof,
    )
    from aisle.harness.frontend_effects import (
        command_probe,
        concurrent_probe,
        continued_probe,
        hosted_probe,
        nested_execution,
    )
    from aisle.harness.frontend_qualification import (
        route_scenario_evidence,
        verify_original_launch,
    )
    from aisle.harness.matched_session import CONTROLLER_FILES, admit_pair
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    operation = run_setup.get("operation", "run") if run_setup is not None else "check"
    assert operation in {"check", "run"}
    assert route == "harness" or operation == "check"
    run_output_name = "session"
    reference_admission = None
    listener_address = ("127.0.0.1", 0)
    if run_setup is not None:
        from datetime import datetime

        from conformance_run_fixture import (
            load_run_setup,
            prepare_run_idea,
            reference_run_admission,
            require_same_run_configuration,
            restore_run_views,
            runtime_references,
            worker_preparation_runs,
        )

        assert shared is None and unified
        run_output_name = run_setup.get("output_name", "session")
        if run_setup.get("reference_session") is not None:
            reference_admission, listener_address = reference_run_admission(
                run_setup["reference_session"]
            )
        assert re.fullmatch(r"session(?:-[a-z0-9-]+)?", run_output_name)
        run_setup = load_run_setup(run_setup["base"])
        assert not (run_setup["root"].parent / "retained" / run_output_name).exists()
        if run_setup["view_baseline"] is None:
            raise ValueError("prepared matrix requires a captured view baseline")
        restore_run_views(
            run_setup["root"].parent,
            run_setup,
            run_setup["view_baseline"],
            run_setup["root"].parent / "retained" / ("views-" + run_output_name),
        )
        if route == "harness" and operation == "run" and not refused:
            prepare_run_idea(run_setup["root"], timestamp=datetime.now(UTC).isoformat())

    binary = Path(binary).resolve()
    version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
    assert version.returncode == 0 and version.stdout.strip() == "codex-cli 0.153.4"
    if shared is not None:
        assert unified and shared["root"] == tmp_path and not shared["active"]
    if run_setup is not None:
        root, views = run_setup["root"], run_setup["views"]
        candidates = copy.deepcopy(run_setup["candidates"])
    elif shared is not None and "prepared" in shared:
        root, baseline, views = shared["prepared"]
        candidates = copy.deepcopy(baseline)
        archive = tmp_path / "retained" / "fixture-state" / f"session-{shared['sequence']:04d}"
        archive.mkdir(parents=True)
        for name, view in views.items():
            _restore_fixture_tree(
                shared["pristine"] / (name + "-view"), view, tmp_path, archive / (name + "-view")
            )
        for name, binding in shared["ambient"].items():
            _restore_fixture_tree(
                shared["pristine"] / (name + "-home"),
                Path(binding["environment"]["HOME"]),
                tmp_path,
                archive / (name + "-home"),
            )
        storage = Path(shared["validation"][0]["snapshot_storage"])
        assert storage.is_relative_to(tmp_path) and storage != tmp_path and not storage.is_symlink()
        shutil.move(storage, archive / "validation-snapshots")
        storage.mkdir()
    else:
        root, candidates, views = prepared_pair(tmp_path)
    evidence_root = tmp_path / "retained"
    evidence_root.mkdir(exist_ok=shared is not None or run_setup is not None)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "registry", root / "registry", dirs_exist_ok=True)
    shutil.copytree(source_root / "src/aisle/nodes", root / "src/aisle/nodes", dirs_exist_ok=True)
    if fault == "denial":
        denied_target = (
            "graphs/expert_t1.yaml" if arm == "typed" else "experts/monolithic/expert_t1.py"
        )
        (views[arm] / denied_target).write_text(
            "nodes: [broken YAML" if arm == "typed" else "invalid python : :"
        )
    target = candidates[arm]["repository"]["editable_allowlist"][0]
    before = (views[arm] / target).read_bytes()
    marker = "# AISLE probe matched-session"
    requests = []
    request_lock = threading.Lock()
    role_steps = {"parent": 0, "child": 0}
    child_finished = threading.Event()
    generated_fixtures = {}
    if route == "mcp" or unified:
        # The same bound launcher selects the representation's existing editable
        # target. It never searches another arm or accepts a caller-selected root.
        targets = sorted(
            {candidate["repository"]["editable_allowlist"][0] for candidate in candidates.values()}
        )
        generated_fixtures = {
            "fixtures/frontend_mcp_fixture.py": Path(probe.__file__)
            .with_name("frontend_mcp_fixture.py")
            .read_bytes(),
            "fixtures/matched_mcp_probe.py": (
                "import os, runpy\nfrom pathlib import Path\n"
                "root = Path.cwd()\n"
                f"targets = {targets!r}\n"
                "selected = [name for name in targets if (root / name).is_file()]\n"
                "if len(selected) != 1: raise ValueError('ambiguous probe target')\n"
                "output = Path(os.environ['HOME']) / 'mcp-probe'\noutput.mkdir()\n"
                "serve = runpy.run_path(str(Path(__file__).with_name("
                "'frontend_mcp_fixture.py')))['serve']\n"
                "serve(output, probe_root=root, probe_target=selected[0], "
                f"probe_marker={marker!r})\n"
            ).encode(),
        }
        for name, raw in generated_fixtures.items():
            (root / name).parent.mkdir(exist_ok=True)
            (root / name).write_bytes(raw)
    frontend_ceiling, effect_attempt = _case_positions(route, unified=unified, refused=refused)
    selector = (
        {"request_id": f"{effect_attempt:08d}"}
        if route == "hosted"
        else {"attempt": effect_attempt}
    )
    if route == "continued_input":
        selector["startup_attempt"] = 2
    scenario = {
        "schema_version": "aisle.frontend-conformance-scenario.v1",
        "route": "harness" if route == "harness_child" else route,
        "arm": arm,
        "case": "quota_refused" if refused else "available",
        "target": target,
        "marker": marker,
        "selector": selector,
    }
    if route in {"harness", "harness_child"}:
        scenario.pop("target")
        scenario.pop("marker")
        scenario["operation"] = operation
    if fault in {"provider_replay", "cancel_before_forward", "cancel_after_forward"}:
        scenario = {
            "schema_version": "aisle.frontend-conformance-scenario.v1",
            "route": "harness",
            "arm": arm,
            "case": "available",
            "operation": "check",
            "selector": {"attempt": 1},
        }
    generated_fixtures["fixtures/scenario.json"] = json.dumps(scenario, sort_keys=True).encode()
    if fault is not None:
        fault_intent = {
            "schema_version": "aisle.frontend-conformance-fault.v1",
            "arm": arm,
            "fault": fault,
            "route": "harness",
            "operation": "check",
            "attempt": 1,
        }
        if fault in {"cancel_before_forward", "cancel_after_forward"}:
            fault_intent = {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": fault,
                "route": "native",
                "attempt": 2,
                "target": target,
                "marker": marker,
            }
        if fault == "concurrent_nested":
            fault_intent = {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": fault,
                "route": "nested",
                "attempts": [frontend_ceiling, frontend_ceiling + 1],
            }
        if fault == "provider_replay":
            fault_intent = {
                "schema_version": "aisle.frontend-conformance-fault.v1",
                "arm": arm,
                "fault": fault,
                "route": "native",
                "earlier_request_id": "00000002",
                "request_id": "00000003",
                "target": target,
                "marker": marker,
            }
        if fault == "denial":
            fault_intent.update(
                target=denied_target,
                sha256=hashlib.sha256((views[arm] / denied_target).read_bytes()).hexdigest(),
            )
        if fault in {"hook_absent", "hook_changed"}:
            fault_intent.update(route="native", attempt=effect_attempt)
            fault_intent.pop("operation")
        generated_fixtures["fixtures/fault.json"] = json.dumps(
            fault_intent, sort_keys=True
        ).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            body = json.loads(raw)
            with request_lock:
                requests.append(body)
                index = len(requests)
            if index == 1:
                frame = _frame(index, "check", {}).replace(
                    b'"name": "check"', b'"namespace": "harness", "name": "check"'
                )
            elif index == 2 or (refused and 2 <= index <= effect_attempt):
                frame = _frame(
                    index,
                    "exec_command",
                    {
                        "cmd": ":"
                        if refused and index < effect_attempt
                        else command_probe(target, marker),
                        "login": False,
                    },
                )
                if route in {"native_edit", "mcp"} and not (refused and index < effect_attempt):
                    frame = _effect_frame(
                        index,
                        route,
                        target=target,
                        marker=marker,
                        before=before,
                        operation=operation,
                        body=body,
                    )
            else:
                frame = probe._events(False)
            if fault == "provider_replay" and index in (2, 3):
                frame = _provider_replay_frame(index, target=target, marker=marker)
            if route == "harness":
                frame = probe._events(False)
                if refused and index < effect_attempt:
                    frame = _frame(index, "exec_command", {"cmd": ":", "login": False})
                elif index == effect_attempt:
                    frame = _frame(index, operation, {}).replace(
                        ('"name": "' + operation + '"').encode(),
                        ('"namespace": "harness", "name": "' + operation + '"').encode(),
                    )
            elif route == "continued_input":
                if index == 2:
                    frame = _frame(
                        index,
                        "exec_command",
                        {
                            "cmd": continued_probe(target),
                            "login": False,
                            "tty": True,
                        },
                    )
                elif refused and 3 <= index < effect_attempt:
                    frame = _frame(index, "exec_command", {"cmd": ":", "login": False})
                elif index == effect_attempt:
                    frame = _effect_frame(
                        index,
                        route,
                        target=target,
                        marker=marker,
                        before=before,
                        operation=operation,
                        body=body,
                    )
            elif route == "nested":
                frame = probe._events(False)
                if index == 1:
                    script = (
                        "await tools.mcp__aisle_harness__check({}); "
                        if unified
                        else "await tools.harness__check({}); "
                    )
                    if refused:
                        script += 'await tools.exec_command({cmd:":",login:false}); ' * (
                            frontend_ceiling - 1
                        )
                    script += (
                        "await tools.exec_command("
                        + json.dumps(
                            {
                                "cmd": command_probe(target, marker),
                                "login": False,
                            }
                        )
                        + ");"
                    )
                    if fault == "concurrent_nested":
                        script = concurrent_probe(
                            frontend_ceiling, mcp_harness=unified, target=target, marker=marker
                        )
                    frame = nested_frame(
                        nested_execution(
                            script,
                            wall_ceiling_s=candidates[arm]["budget"]["tool_wall_ceiling_s"],
                        )
                    )
            elif route == "hosted" and index == 2 and not refused:
                request = requests[-1]
                intent = hosted_probe(target, marker)
                assert request["max_tool_calls"] == frontend_ceiling - 1
                contract = _research_contract(
                    candidates, route=route, unified=unified, target=target, marker=marker
                )
                assert contract.splitlines()[-1] in json.dumps(request["input"]).replace('\\"', '"')
                with (views[arm] / target).open("ab") as stream:
                    stream.write(marker.encode() + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                events = [
                    json.loads(line[6:])
                    for line in probe._events(False).splitlines()
                    if line.startswith(b"data: ")
                ]
                item = {
                    "type": "web_search_call",
                    "id": "search-probe",
                    "status": "completed",
                    "action": {"type": "search", "query": intent},
                }
                for event in events:
                    if "output_index" in event:
                        event["output_index"] += 1
                    if event["type"] == "response.completed":
                        event["response"]["output"].insert(0, item)
                events.insert(
                    1, {"type": "response.output_item.done", "output_index": 0, "item": item}
                )
                frame = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
                ).encode()
            elif route in {"subagents", "harness_child"}:
                child = any(
                    row.get("type") == "agent_message"
                    and row.get("recipient") == "/root/coverage_child"
                    and "AISLE_CHILD_ROUTE" in json.dumps(row.get("content"))
                    for row in body["input"]
                )
                role = "child" if child else "parent"
                with request_lock:
                    role_steps[role] += 1
                    step = role_steps[role]
                frame = probe._events(False).replace(b"resp_fixture", f"resp_done_{index}".encode())
                child_effect_step = effect_attempt - 2
                if child and step <= child_effect_step:
                    frame = _frame(
                        index,
                        "exec_command",
                        {
                            "cmd": ":"
                            if step < child_effect_step
                            else command_probe(target, marker),
                            "login": False,
                        },
                    )
                    if route == "harness_child" and step == child_effect_step:
                        frame = _frame(index, "check", {}).replace(
                            b'"name": "check"',
                            b'"namespace": "mcp__aisle_harness", "name": "check"',
                        )
                    if unified and refused and step == child_effect_step - 1:
                        frame = _batch_frames(
                            frame,
                            _effect_frame(
                                index + 1,
                                route,
                                target=target,
                                marker=marker,
                                before=before,
                                operation=operation,
                                body=body,
                            ),
                        )
                        child_finished.set()
                    if refused and step == child_effect_step:
                        child_finished.set()
                elif child:
                    child_finished.set()
                elif step == 1:
                    frame = _child_start_frame(
                        index, wall_ceiling_s=candidates[arm]["budget"]["tool_wall_ceiling_s"]
                    )
                elif not child_finished.is_set():
                    self.send_error(400, "child fixture did not complete")
                    return
            if (
                unified
                and refused
                and index == effect_attempt - 1
                and route not in {"nested", "hosted", "subagents", "harness_child"}
            ):
                frame = _batch_frames(
                    frame,
                    _effect_frame(
                        effect_attempt,
                        route,
                        target=target,
                        marker=marker,
                        before=before,
                        operation=operation,
                        body=body,
                    ),
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)

    if shared is None:
        server_class = (
            ThreadingHTTPServer if route in {"subagents", "harness_child"} else HTTPServer
        )
        server = server_class(listener_address, Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        session_id = "matched-conformance"
        output_name = "session"
        if run_setup is not None:
            output_name = run_output_name
            session_id = "matched-conformance-" + run_output_name
    else:
        shared["handler"] = Handler
        shared["active"] = True
        shared["sequence"] += 1
        server = shared["server"]
        session_id = f"matched-conformance-{shared['sequence']:04d}"
        output_name = f"session-{shared['sequence']:04d}"
    try:
        if run_setup is not None:
            bindings, ambient = (
                copy.deepcopy(run_setup["confinement"]),
                copy.deepcopy(run_setup["ambient"]),
            )
        elif shared is not None and "prepared" in shared:
            bindings, ambient = copy.deepcopy(shared["bindings"]), copy.deepcopy(shared["ambient"])
        else:
            bindings = _confinement_pair(tmp_path, root, candidates, views)
            ambient = _ambient_pair(candidates, bindings)
            if shared is not None:
                shared["prepared"] = (root, copy.deepcopy(candidates), views)
                shared["bindings"], shared["ambient"] = (
                    copy.deepcopy(bindings),
                    copy.deepcopy(ambient),
                )
                shared["pristine"] = root / "conformance-pristine"
                shared["pristine"].mkdir()
                for name, view in views.items():
                    shutil.copytree(view, shared["pristine"] / (name + "-view"))
                    shutil.copytree(
                        Path(ambient[name]["environment"]["HOME"]),
                        shared["pristine"] / (name + "-home"),
                    )
        adapter = tmp_path / "synthetic-adapter"
        adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
        adapter.chmod(0o755)
        launches = {}
        for name, candidate in candidates.items():
            candidate["agent"].update(
                cli_revision=version.stdout.strip(),
                cli_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            )
            model = "gpt-5.4" if route == "native_edit" or unified else "aisle-fixture"
            candidate["model"].update(requested_identity=model, served_identity=model)
            candidate["confinement"]["adapter_binary_sha256"] = hashlib.sha256(
                adapter.read_bytes()
            ).hexdigest()
            if run_setup is None:
                candidate["runtime_binaries"].append(
                    {
                        "name": "harness-python",
                        "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                    }
                )
                candidate["policy"]["allowed_external_tools"].append("harness.check")
            candidate["budget"].update(
                tool_ceiling=4 if unified else 2 if route == "harness_child" else 1,
                tool_wall_ceiling_s=candidate["budget"]["tool_wall_ceiling_s"]
                if run_setup is not None
                else 30,
                wall_ceiling_s=candidate["budget"]["wall_ceiling_s"]
                if run_setup is not None
                else 10
                if fault == "controller_timeout"
                else 30,
                frontend_tool_ceiling=frontend_ceiling,
                frontend_coverage="observed",
            )
            (Path(ambient[name]["environment"]["HOME"]) / "tool-channel").mkdir()
            launches[name] = {
                "argv": [str(binary), "app-server", "--listen", "stdio://"],
                "app_server": {
                    "baseInstructions": "system prompt",
                    "developerInstructions": "research contract",
                },
                "tool_python": sys.executable,
                "provider": {
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "requires_openai_auth": False,
                },
            }
            if host is not None:
                host_path = Path(host).resolve()
                host_hash = hashlib.sha256(host_path.read_bytes()).hexdigest()
                launches[name]["code_mode_host"] = {"path": str(host_path), "sha256": host_hash}
                candidate["runtime_binaries"].append(
                    {"name": "codex-code-mode-host", "sha256": host_hash}
                )
            if route == "mcp" or unified:
                settings = {
                    "mcp_servers.aisle_fixture.command": str(Path(sys.executable).resolve()),
                    "mcp_servers.aisle_fixture.args": [
                        "-I",
                        "-B",
                        str(root / "fixtures/matched_mcp_probe.py"),
                    ],
                }
                for key, value in settings.items():
                    launches[name]["argv"].extend(["-c", key + "=" + json.dumps(value)])
            if route == "hosted" or unified:
                launches[name]["provider"]["hosted_tool_contract"] = (
                    "aisle.fixture.responses.max_tool_calls.v1"
                )
                launches[name]["argv"].extend(["-c", 'web_search="live"'])
                contract = _research_contract(
                    candidates, route=route, unified=unified, target=target, marker=marker
                )
                launches[name]["app_server"]["developerInstructions"] = contract
                candidate["prompts"]["research_contract_sha256"] = hashlib.sha256(
                    contract.encode()
                ).hexdigest()
            if route in {"subagents", "harness_child"} or unified:
                launches[name]["argv"].extend(
                    ["-c", "features.multi_agent=true", "-c", "features.multi_agent_v2=true"]
                )
                if route == "harness_child" or unified:
                    launches[name]["mcp_harness"] = True
            if fault is not None:
                launches[name] = fault_launch(launches[name], fault)
        declared = bindings[arm]["policy"]
        policy = MacOSPolicy(
            **{
                key: value if key == "network_policy" else tuple(Path(p) for p in value)
                for key, value in declared.items()
            }
        )
        compiled = compile_macos_profile(policy)
        profile_path = tmp_path / "launch.sb"
        profile_path.write_text(compiled.text)
        attestation = _attestation(compiled, profile_path, adapter)
        if run_setup is not None:
            validation, runtime = run_setup["typed_validation"], run_setup["tool_runtime"]
        elif shared is not None and "validation" in shared:
            validation, runtime = copy.deepcopy(shared["validation"])
        else:
            validation, runtime = validation_binding(
                tmp_path / "validation", hidden=(root, evidence_root, *views.values())
            )
            if shared is not None:
                shared["validation"] = copy.deepcopy((validation, runtime))
        sources = [
            Path(__file__),
            Path(probe.__file__),
            Path(sys.modules[_frame.__module__].__file__),
            Path(sys.modules[nested_frame.__module__].__file__),
            *(
                Path(sys.modules[name].__file__)
                for name in (
                    "test_matched_session",
                    "test_treatment_integrity",
                    "test_treatment_confinement",
                    "test_typed_validation_binding",
                    "test_typed_validation_launch",
                    "test_typed_validation_snapshot",
                    "test_monolith_worker_launch",
                )
            ),
        ]
        extra_execution = {}
        if run_setup is not None:
            sources.append(Path(sys.modules["conformance_run_fixture"].__file__))
            generated_fixtures["fixtures/view-baseline.json"] = json.dumps(
                run_setup["view_baseline"], sort_keys=True
            ).encode()
            generated_fixtures["fixtures/worker-preparations.json"] = json.dumps(
                runtime_references(run_setup["worker_preparations"]), sort_keys=True
            ).encode()
            extra_execution = {key: run_setup[key] for key in ("development", "run_controller")}
        fixture_files = {"fixtures/" + source.name: source.read_bytes() for source in sources}
        fixture_files.update(generated_fixtures)
        for name, raw in fixture_files.items():
            (root / name).parent.mkdir(exist_ok=True)
            (root / name).write_bytes(raw)
        profile = {
            "schema_version": "aisle.frontend-conformance.v1",
            "frontend": candidates[arm]["agent"],
            "execution_sha256": execution_digest(
                {
                    "typed_validation": validation,
                    "tool_runtime": runtime,
                    "confinement_bindings": bindings,
                    "ambient_bindings": ambient,
                    **extra_execution,
                }
            ),
            "bindings": {
                name: configuration_digest(candidates[name], launches[name]) for name in candidates
            },
            "controller_files": {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in CONTROLLER_FILES
            },
            "fixture_files": {
                name: hashlib.sha256(raw).hexdigest() for name, raw in fixture_files.items()
            },
            "routes": {
                name: {"counting_unit": unit, "proofs": []} for name, unit in ROUTE_UNITS.items()
            },
        }
        raw_profile = json.dumps(profile).encode()
        (root / "profile.json").write_bytes(raw_profile)
        for launch in launches.values():
            launch["conformance_profile"] = {
                "path": "profile.json",
                "sha256": hashlib.sha256(raw_profile).hexdigest(),
            }
        plan = admit_pair(
            root,
            candidates,
            views,
            confinement=bindings,
            ambient=ambient,
            launches=launches,
            typed_validation=validation,
            tool_runtime=runtime,
            **extra_execution,
        )
        if reference_admission is not None:
            require_same_run_configuration(plan, reference_admission, fault=fault)
        output = evidence_root / output_name
        # Explicit synthetic input, not an operating-system access attestation.
        access_log = _access_log(tmp_path)
        record = run_engineering_session(
            plan,
            root,
            views,
            arm,
            output,
            session_id=session_id,
            profile_path=profile_path,
            attestation=attestation,
            hidden_access_log=access_log,
            **(
                {
                    "worker_preparations": worker_preparation_runs(
                        run_setup["worker_preparations"], arm
                    )
                }
                if run_setup is not None
                else {}
            ),
        )
    finally:
        if shared is None:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            assert not worker.is_alive()
        else:
            with shared["condition"]:
                assert shared["condition"].wait_for(lambda: shared["inflight"] == 0, timeout=5)
            shared["active"] = False

    # Acquire original receipt bytes, including admission and both file snapshots.
    files = {"proof/" + name: raw for name, raw in acquire_session_inputs(output).items()}
    profile["proof_files"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    files.update({name: (root / name).read_bytes() for name in CONTROLLER_FILES})
    files.update(fixture_files)
    proof = read_session_proof(
        {"profile": profile, "files": files},
        "proof/matched-session.json",
        candidate=candidates[arm],
        launch=launches[arm],
        arm=arm,
    )
    if shared is not None:
        shared["proofs"].append(proof)
    assert (
        proof["artifacts"]["conformance/inputs/fixtures/scenario.json"]
        == generated_fixtures["fixtures/scenario.json"]
    )
    launch = json.loads(proof["artifacts"]["launch.json"])
    assert launch["cwd"] == str(views[arm])
    assert launch["argv"] == launches[arm]["argv"]
    assert record["eligible_for_estimate"] is False
    assert verify_original_launch(proof)["launch_verified"] is True
    if fault in {"cancel_before_forward", "cancel_after_forward"}:
        from aisle.harness.frontend_qualification import audit_provider_interruption

        result = audit_provider_interruption(proof)
        assert result["fault_verified"] and result["charged_attempts"] == [1, 2]
        assert result["delivery_uncertain"] and not result["complete_coverage"]
        _reject_changed_interruption_evidence(proof)
        _reject_changed_unused_host_failure(proof, audit_provider_interruption)
        return
    if fault == "concurrent_nested":
        from aisle.harness.frontend_qualification import audit_concurrent_nested

        result = audit_concurrent_nested(proof)
        assert result["fault_verified"] and result["overlap_verified"]
        assert result["effect"] == "unchanged" and not result["complete_coverage"]
        _reject_changed_concurrency_evidence(proof)
        if proof["record"]["ok"] is False:
            _reject_changed_failed_host_refusal(proof)
        return
    if fault == "provider_replay":
        from aisle.harness.frontend_qualification import audit_provider_replay

        result = audit_provider_replay(proof)
        assert result["fault_verified"] and result["effect"] == "unchanged"
        assert result["controller_attempts"] == 1 and not result["complete_coverage"]
        _reject_changed_replay_evidence(proof)
        _reject_changed_unused_host_failure(proof, audit_provider_replay)
        return
    if fault in {"hook_absent", "hook_changed"}:
        from aisle.harness.frontend_qualification import audit_hook_independence

        result = audit_hook_independence(proof)
        assert result["fault_verified"] is True
        assert result["effect"] == "unchanged"
        assert result["complete_coverage"] is False
        return
    if fault == "denial":
        from aisle.harness.frontend_qualification import audit_controller_denial

        result = audit_controller_denial(proof)
        assert result["fault_verified"] is True and result["controller_attempts"] == 1
        assert result["frontend_success"] is False
        assert result["complete_coverage"] is False
        return
    if fault in {"controller_unavailable", "controller_timeout", "malformed_response"}:
        from aisle.harness.frontend_qualification import audit_controller_fault

        assert record["ok"] is False
        assert not proof["artifacts"]["tool-events.jsonl"]
        assert not any(name.startswith("request-") for name in proof["artifacts"])
        result = audit_controller_fault(proof)
        assert result["fault_verified"] is True
        assert result["controller_attempts"] == 0
        assert result["complete_coverage"] is False
        return
    result = route_scenario_evidence(proof)
    assert result["case_verified"] is True
    if refused and unified and route != "nested":
        _reject_changed_unused_host_failure(proof, route_scenario_evidence)
    if route == "nested":
        assert len(result["audit"]["nested_harness_links"]) == 1
        if unified and refused:
            _reject_changed_failed_host_refusal(proof)
    if route in {"harness", "harness_child"}:
        assert result["effect"]["effect"] == ("not_dispatched" if refused else "controller_result")
        if not refused:
            attempts = [
                json.loads(raw)["record"]
                for raw in proof["artifacts"]["tool-events.jsonl"].splitlines()
                if "record" in json.loads(raw)
            ]
            assert attempts and all(row["ok"] is True for row in attempts)
            if arm == "typed" and operation == "check":
                assert "tool-000001/validation/runtime.json" in proof["artifacts"]
        if refused:
            assert result["audit"]["authorized_harness_attempts"] == []
            if route == "harness":
                assert not any(name.startswith("request-") for name in proof["artifacts"])
        if route == "harness_child":
            protocol = json.loads(proof["artifacts"]["frontend-protocol-reference.json"])
            frames = [
                json.loads(raw)
                for name, raw in proof["artifacts"].items()
                if re.fullmatch(r"frontend-protocol/[0-9]{8}-received.json", name)
            ]
            calls = [
                row["params"]
                for row in frames
                if row.get("method") == "item/started"
                and row.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
            ]
            assert len(calls) == 1 and calls[0]["threadId"] != protocol["thread_id"]
            assert calls[0]["item"]["server"] == "aisle_harness"
    else:
        assert result["effect"]["effect"] == ("unchanged" if refused else "appended")
    assert result["complete_coverage"] is False
    if route == "hosted":
        assert len(requests) == (frontend_ceiling if refused else 2)
    elif route in {"subagents", "harness_child"}:
        assert role_steps["child"] >= 2


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_controller_unavailable_retains_actual_frontend_request(
    tmp_path, monkeypatch, arm, *, run_setup=None, unified=False
):
    """MON-12/MON-13: actual frontend request stops before an unavailable controller runs."""
    from aisle.harness.matched_tool_service import ToolService

    def unavailable(service):
        service.error = "injected controller unavailable"
        service.failed.set()

    monkeypatch.setattr(ToolService, "_serve", unavailable)
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path,
        arm,
        False,
        "harness",
        fault="controller_unavailable",
        run_setup=run_setup,
        unified=unified,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_controller_timeout_retains_actual_frontend_request(
    tmp_path, monkeypatch, arm, *, run_setup=None, unified=False
):
    """MON-12/MON-13: a nonresponsive service times out without consuming its request grant."""
    from aisle.harness.matched_tool_service import ToolService

    def stalled(service):
        service.stop.wait()

    monkeypatch.setattr(ToolService, "_serve", stalled)
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path,
        arm,
        False,
        "harness",
        fault="controller_timeout",
        run_setup=run_setup,
        unified=unified,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_malformed_response_retains_rejected_bytes(
    tmp_path, monkeypatch, arm, *, run_setup=None, unified=False
):
    """MON-12/MON-13: malformed controller output never becomes an actual frontend result."""
    from aisle.harness.matched_tool_service import ToolService

    monkeypatch.setattr(ToolService, "_serve", lambda service: service.stop.wait())
    monkeypatch.setattr(
        ToolService,
        "controller_response",
        lambda service, identity: {
            "request_id": identity,
            "ok": "false",
            "classification": "tool_result",
            "result": {"ok": False},
            "error": None,
            "attempt": 1,
        },
    )
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path,
        arm,
        False,
        "harness",
        fault="malformed_response",
        run_setup=run_setup,
        unified=unified,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_tool_denial_reaches_actual_frontend(
    tmp_path, arm, *, run_setup=None, unified=False
):
    """MON-12/MON-13: a real negative arm check reaches the frontend without success promotion."""
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path, arm, False, "harness", fault="denial", run_setup=run_setup, unified=unified
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("fault", ["hook_absent", "hook_changed"])
def test_admitted_budget_refusal_is_independent_of_frontend_hook(
    tmp_path, arm, fault, *, run_setup=None, unified=False
):
    """MON-8/MON-13: absent or failing hooks cannot permit an over-budget native effect."""
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path, arm, True, "native", fault=fault, run_setup=run_setup, unified=unified
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_provider_replay_retains_unexecuted_effect(
    tmp_path, arm, *, run_setup=None, unified=False
):
    """MON-13: a new response reusing an executed call cannot release its changed command."""
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path, arm, True, "native", fault="provider_replay", run_setup=run_setup, unified=unified
    )


def _reject_changed_replay_evidence(proof):
    """MON-13: semantically changed, hash-consistent protocol records must be rejected."""
    import copy

    from aisle.harness.frontend_qualification import audit_provider_replay

    for fault in ("snapshot", "native_exit", "reply", "duplicate_completion"):
        changed = copy.deepcopy(proof)
        artifacts = changed["artifacts"]
        if fault == "snapshot":
            changed["record"]["snapshots"]["final"] = {}
        else:
            for name, raw in list(artifacts.items()):
                if not re.fullmatch(r"frontend-protocol/[0-9]{8}-(received|sent).json", name):
                    continue
                row = json.loads(raw)
                if (
                    fault in {"native_exit", "duplicate_completion"}
                    and row.get("method") == "item/completed"
                    and row.get("params", {}).get("item", {}).get("type") == "commandExecution"
                ):
                    if fault == "native_exit":
                        row["params"]["item"]["exitCode"] = 1
                    else:
                        # Replace another received event with a second identical completion.
                        alternate = next(
                            key
                            for key in artifacts
                            if key != name
                            and re.fullmatch(r"frontend-protocol/[0-9]{8}-received.json", key)
                            and json.loads(artifacts[key]).get("method") == "item/started"
                            and json.loads(artifacts[key])
                            .get("params", {})
                            .get("item", {})
                            .get("type")
                            == "commandExecution"
                        )
                        name = alternate
                elif (
                    fault == "reply"
                    and type(row.get("result")) is dict
                    and "success" in row["result"]
                ):
                    row["result"]["success"] = False
                else:
                    continue
                replacement = json.dumps(row).encode()
                artifacts[name] = replacement
                reference = json.loads(artifacts["frontend-protocol-reference.json"])
                reference["artifacts"][name.removeprefix("frontend-protocol/")] = hashlib.sha256(
                    replacement
                ).hexdigest()
                reference["bytes"] = sum(
                    len(artifacts["frontend-protocol/" + key]) for key in reference["artifacts"]
                )
                artifacts["frontend-protocol-reference.json"] = json.dumps(reference).encode()
                break
            else:
                raise AssertionError("missing mutation target: " + fault)
        with pytest.raises(ValueError):
            audit_provider_replay(changed)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_concurrent_nested_calls_share_last_slot(
    tmp_path, arm, *, run_setup=None, unified=False
):
    """MON-8/MON-13: overlapping actual nested calls cannot share the last reservation."""
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path,
        arm,
        True,
        "nested",
        fault="concurrent_nested",
        run_setup=run_setup,
        unified=unified,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_concurrent_nested_with_combined_frontend_features(tmp_path, arm):
    """MON-8/MON-13: the unified frontend retains concurrent last-slot refusal evidence."""
    test_admitted_concurrent_nested_calls_share_last_slot(tmp_path, arm, unified=True)


def _reject_changed_concurrency_evidence(proof):
    """MON-13: valid sequential receipts and failed completions cannot prove concurrent success."""
    import base64
    import copy

    from aisle.harness._code_mode_protocol import message
    from aisle.harness.frontend_qualification import audit_concurrent_nested

    ceiling = proof["admission"]["arms"][proof["record"]["arm"]]["budget"]["frontend_tool_ceiling"]

    for fault in ("sequential", "failed_completion", "observation_deadline"):
        changed = copy.deepcopy(proof)
        artifacts = changed["artifacts"]
        reference = json.loads(artifacts["code-mode-reference.json"])
        names = sorted(reference["proxy"]["artifacts"])
        rows = [json.loads(artifacts["code-mode/rpc/" + name]) for name in names]
        first = message("ToolCall", artifacts[f"frontend-dispatch/{ceiling:08d}.frame"])
        completion = next(
            i
            for i, row in enumerate(rows)
            if row["method"] == "CompleteToolCall"
            and row["phase"] == "request"
            and message("CompleteToolCallRequest", base64.b64decode(row["frame"])).invocation_id
            == first.invocation_id
        )
        if fault == "sequential":
            second = next(
                i
                for i, row in enumerate(rows)
                if row["method"] == "SubscribeToToolCalls"
                and row["phase"] == "response"
                and base64.b64decode(row["frame"])
                == artifacts[f"frontend-dispatch/{ceiling + 1:08d}.frame"]
            )
            rpc = rows[completion]["rpc"]
            block = [row for row in rows if row["rpc"] == rpc]
            # A verified completion RPC can end in cancellation after its
            # request was retained. Move its whole original block in either
            # lifecycle, preserving a valid but sequential source transcript.
            assert block[0]["phase"] == "request"
            assert block[-1]["phase"] in {"returned", "cancelled"}
            assert second < completion
            rows = rows[:second] + block + [row for row in rows[second:] if row["rpc"] != rpc]
        elif fault == "observation_deadline":
            execution = next(
                row for row in rows if row["method"] == "Execute" and row["phase"] == "request"
            )
            value = message("ExecuteRequest", base64.b64decode(execution["frame"]))
            value.yield_time_ms += 1
            execution["frame"] = base64.b64encode(value.SerializeToString()).decode()
        else:
            value = message("CompleteToolCallRequest", base64.b64decode(rows[completion]["frame"]))
            result = json.loads(value.succeeded.output_json)
            result["exit_code"] = 1
            value.succeeded.output_json = json.dumps(result).encode()
            rows[completion]["frame"] = base64.b64encode(value.SerializeToString()).decode()
        for name, row in zip(names, rows, strict=True):
            raw = json.dumps(row).encode()
            artifacts["code-mode/rpc/" + name] = raw
            reference["proxy"]["artifacts"][name] = hashlib.sha256(raw).hexdigest()
        reference["proxy"]["bytes"] = sum(len(artifacts["code-mode/rpc/" + name]) for name in names)
        artifacts["code-mode-reference.json"] = json.dumps(reference).encode()
        reason = (
            "RPC order does not prove overlapping"
            if fault == "sequential"
            else "concurrent script differs"
            if fault == "observation_deadline"
            else "command did not finish successfully"
        )
        with pytest.raises(ValueError, match=reason):
            audit_concurrent_nested(changed)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("fault", ["cancel_before_forward", "cancel_after_forward"])
def test_admitted_interrupted_delivery_keeps_reservation(
    tmp_path, monkeypatch, arm, fault, *, run_setup=None, unified=False
):
    """MON-12/MON-13: actual source forwarding cancellation remains charged and retires dispatch."""
    import asyncio

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    original = DispatchBudget.dispatch
    retired = []

    def dispatch(budget, call, frame, deliver):
        if not call["tool_name"].startswith("["):
            return original(budget, call, frame, deliver)

        def fail(raw):
            if fault == "cancel_after_forward":
                deliver(raw)
            raise asyncio.CancelledError("injected provider delivery cancellation")

        try:
            return original(budget, call, frame, fail)
        except asyncio.CancelledError:
            with pytest.raises(DispatchRefused, match="closed"):
                original(
                    budget,
                    {
                        "turn_id": "after-cancellation",
                        "call_id": "later",
                        "tool_name": "exec_command",
                    },
                    b"later",
                    lambda _: pytest.fail("retired authority delivered work"),
                )
            retired.append(True)
            raise

    monkeypatch.setattr(DispatchBudget, "dispatch", dispatch)
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path, arm, False, "native", fault=fault, run_setup=run_setup, unified=unified
    )
    assert retired == [True]


def _reject_changed_unused_host_failure(proof, audit):
    """MON-13: a native fault cannot excuse an unrelated unused-host failure."""
    raw = proof["artifacts"].get("code-mode-reference.json")
    if raw is None or json.loads(raw)["failure"] is None:
        return
    for field in ("host", "proxy"):
        changed = copy.deepcopy(proof)
        reference = json.loads(raw)
        target = reference if field == "host" else reference["proxy"]
        target["failure"] = "injected unrelated failure"
        changed["artifacts"]["code-mode-reference.json"] = json.dumps(reference).encode()
        with pytest.raises(ValueError, match="unrelated or active nested host failure"):
            audit(changed)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize(
    "fault", ["cancel_before_forward", "cancel_after_forward", "provider_replay"]
)
def test_native_fault_with_combined_frontend_features(tmp_path, monkeypatch, arm, fault):
    """MON-12/MON-13: native fault proofs cover the complete enabled feature configuration."""
    if fault == "provider_replay":
        test_admitted_provider_replay_retains_unexecuted_effect(tmp_path, arm, unified=True)
    else:
        test_admitted_interrupted_delivery_keeps_reservation(
            tmp_path, monkeypatch, arm, fault, unified=True
        )


def _reject_changed_interruption_evidence(proof):
    """MON-13: altered delivery extent or a promoted uncertain charge cannot qualify."""
    import copy

    from aisle.harness.frontend_qualification import audit_provider_interruption

    for fault in ("forwarded", "promoted"):
        changed = copy.deepcopy(proof)
        artifacts = changed["artifacts"]
        directory = "provider" if fault == "forwarded" else "frontend-dispatch"
        name = "00000002-delivery.json"
        row = json.loads(artifacts[directory + "/" + name])
        if fault == "forwarded":
            row["events_returned"] = 3 - row["events_returned"]
        else:
            row.update(status="returned", error_type=None)
        artifacts[directory + "/" + name] = json.dumps(row).encode()
        reference_name = directory + "-reference.json"
        reference = json.loads(artifacts[reference_name])
        reference["artifacts"][name] = hashlib.sha256(artifacts[directory + "/" + name]).hexdigest()
        if "bytes" in reference:
            reference["bytes"] = sum(
                len(artifacts[directory + "/" + key]) for key in reference["artifacts"]
            )
        artifacts[reference_name] = json.dumps(reference).encode()
        with pytest.raises(ValueError, match="exact charged boundary"):
            audit_provider_interruption(changed)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_native_call_with_combined_frontend_features(tmp_path, arm):
    """MON-8/MON-13: native availability must survive the full proposed feature configuration."""
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path, arm, False, "native", unified=True
    )


def test_shared_native_cases_keep_one_execution_and_launch_binding(tmp_path):
    """MON-8/MON-13: paired cases share configuration and roots while retaining separate
    receipts.
    """
    from aisle.harness.frontend_conformance import execution_digest

    with _shared_frontend_fixture(tmp_path) as shared:
        for arm in ("typed", "monolithic"):
            test_admitted_actual_session_composes_source_execution_and_effect(
                tmp_path, arm, False, "native", unified=True, shared=shared
            )
        first, second = shared["proofs"]
        assert first["record"]["session_id"] != second["record"]["session_id"]
        assert execution_digest(first["admission"]) == execution_digest(second["admission"])
        profiles = [
            json.loads(proof["artifacts"]["conformance/profile.json"]) for proof in shared["proofs"]
        ]
        assert profiles[0]["bindings"] == profiles[1]["bindings"]
        assert profiles[0]["execution_sha256"] == profiles[1]["execution_sha256"]
        assert all(proof["record"]["ok"] for proof in shared["proofs"])
        from aisle.harness.frontend_qualification import route_scenario_evidence

        # Resetting private working state must not invalidate the earlier receipt.
        assert route_scenario_evidence(first)["case_verified"]


def _restore_fixture_tree(pristine, destination, root, archive):
    """Restore only an idle fixture's generated view or HOME; retain all session outputs."""
    assert destination.is_relative_to(root) and destination != root
    assert archive.is_relative_to(root / "retained") and not archive.exists()
    if destination.exists() or destination.is_symlink():
        shutil.move(destination, archive)
    shutil.copytree(pristine, destination)


@contextmanager
def _shared_frontend_fixture(root):
    """Own one listener and stable execution inputs across sequential conformance cases."""
    state = {
        "root": root,
        "active": False,
        "sequence": 0,
        "proofs": [],
        "inflight": 0,
        "condition": threading.Condition(),
    }

    def handler(*args):
        with state["condition"]:
            state["inflight"] += 1
        try:
            state["handler"](*args)
        finally:
            with state["condition"]:
                state["inflight"] -= 1
                state["condition"].notify_all()

    class Server(ThreadingHTTPServer):
        daemon_threads = False

    server = Server(("127.0.0.1", 0), handler)
    state["server"] = server
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05))
    worker.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive() and state["inflight"] == 0


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_admitted_nested_mcp_with_combined_frontend_features(tmp_path, arm):
    """MON-12/MON-13: nested owned MCP results bind through the real frontend and controller."""
    test_admitted_actual_session_composes_source_execution_and_effect(
        tmp_path, arm, False, "nested", unified=True
    )


def test_shared_nested_mcp_refusals_keep_one_binding(tmp_path):
    """MON-8/MON-13: both actual arms refuse nested work after exhausting the same four slots."""
    with _shared_frontend_fixture(tmp_path) as shared:
        for arm in ("typed", "monolithic"):
            test_admitted_actual_session_composes_source_execution_and_effect(
                tmp_path, arm, True, "nested", unified=True, shared=shared
            )
        profiles = [
            json.loads(proof["artifacts"]["conformance/profile.json"]) for proof in shared["proofs"]
        ]
        assert profiles[0]["bindings"] == profiles[1]["bindings"]
        assert profiles[0]["execution_sha256"] == profiles[1]["execution_sha256"]


def _reject_changed_failed_host_refusal(proof):
    """MON-12/MON-13: the combined failure never becomes clean-host evidence."""
    from aisle.harness.code_mode_audit import verify_code_mode_hosted_refusal
    from aisle.harness.frontend_qualification import _authority_evidence

    with pytest.raises(ValueError, match="did not complete"):
        _authority_evidence(proof["artifacts"])
    authority = _authority_evidence(proof["artifacts"], refusal=True)
    assert authority["failed_host_refusal"]["refused_nested_attempts"] == [5]
    assert authority["failed_host_refusal"]["linked_hosted_requests"] == ["00000002"]
    provider = authority["provider"]
    delegates = {
        tuple(row)
        for row in json.loads(provider["artifacts"]["invocation.json"])["delegated_tools"]
    }
    args = dict(
        authority["code_mode"],
        dispatch=authority["dispatch"],
        host_failure=authority["failed_host_refusal"]["host_failure"],
        provider=provider,
        provider_delegated_tools=delegates,
    )
    for fault in ("host", "provider", "proxy"):
        changed = copy.deepcopy(args)
        if fault == "host":
            changed["host_failure"] = "ValueError: other failure"
        elif fault == "provider":
            changed["provider"]["expected"]["failure"] = None
        else:
            changed["expected"]["failure"] = "CancelledError"
        assert not verify_code_mode_hosted_refusal(**changed)["ok"]


def test_shared_monolithic_nested_available_and_refused(tmp_path):
    """MON-8/MON-12/MON-13: one monolithic configuration binds available and refused nested
    effects.
    """
    from aisle.harness.frontend_qualification import route_scenario_evidence

    with _shared_frontend_fixture(tmp_path) as shared:
        for refused in (False, True):
            test_admitted_actual_session_composes_source_execution_and_effect(
                tmp_path, "monolithic", refused, "nested", unified=True, shared=shared
            )
        first, second = shared["proofs"]
        profiles = [
            json.loads(proof["artifacts"]["conformance/profile.json"]) for proof in shared["proofs"]
        ]
        assert profiles[0]["bindings"] == profiles[1]["bindings"]
        assert profiles[0]["execution_sha256"] == profiles[1]["execution_sha256"]
        assert first["record"]["ok"] is True and second["record"]["ok"] is False
        assert route_scenario_evidence(first)["effect"]["effect"] == "appended"
        assert route_scenario_evidence(second)["effect"]["effect"] == "unchanged"


def test_prepared_harness_run_through_actual_frontend():
    """MON-8/MON-12/MON-13: selected route cases use the same prepared execution context."""
    selected = os.environ.get("AISLE_PREPARED_RUN_ROOT")
    if selected is None:
        pytest.skip("requires a fresh, explicitly prepared simulation controller")
    arm = os.environ.get("AISLE_PREPARED_RUN_ARM", "monolithic")
    assert arm in {"typed", "monolithic"}
    route = os.environ.get("AISLE_PREPARED_RUN_ROUTE", "harness")
    assert route in {
        "harness",
        "harness_child",
        "native",
        "native_edit",
        "continued_input",
        "nested",
        "mcp",
        "hosted",
        "subagents",
    }
    operation = os.environ.get(
        "AISLE_PREPARED_RUN_OPERATION", "run" if route == "harness" else "check"
    )
    assert operation in {"check", "run"} and (route == "harness" or operation == "check")
    refused = os.environ.get("AISLE_PREPARED_RUN_REFUSED", "0")
    assert refused in {"0", "1"}
    base = Path(selected).resolve(strict=True)
    test_admitted_actual_session_composes_source_execution_and_effect(
        base,
        arm,
        refused == "1",
        route,
        unified=True,
        run_setup={
            "base": base,
            "operation": operation,
            "reference_session": os.environ.get("AISLE_PREPARED_RUN_REFERENCE"),
            "output_name": os.environ.get("AISLE_PREPARED_RUN_OUTPUT", "session"),
        },
    )


def test_prepared_fault_through_actual_frontend():
    """MON-13: run the existing fault injection against one prepared execution context."""
    selected = os.environ.get("AISLE_PREPARED_RUN_ROOT")
    fault = os.environ.get("AISLE_PREPARED_RUN_FAULT")
    if selected is None or fault is None:
        pytest.skip("requires a prepared controller and an explicitly selected fault")
    base = Path(selected).resolve(strict=True)
    arm = os.environ.get("AISLE_PREPARED_RUN_ARM", "monolithic")
    assert arm in {"typed", "monolithic"}
    cases = {
        "controller_unavailable": (
            test_admitted_controller_unavailable_retains_actual_frontend_request,
            True,
            False,
        ),
        "controller_timeout": (
            test_admitted_controller_timeout_retains_actual_frontend_request,
            True,
            False,
        ),
        "malformed_response": (
            test_admitted_malformed_response_retains_rejected_bytes,
            True,
            False,
        ),
        "denial": (test_admitted_tool_denial_reaches_actual_frontend, False, False),
        "hook_absent": (test_admitted_budget_refusal_is_independent_of_frontend_hook, False, True),
        "hook_changed": (test_admitted_budget_refusal_is_independent_of_frontend_hook, False, True),
        "provider_replay": (test_admitted_provider_replay_retains_unexecuted_effect, False, False),
        "concurrent_nested": (test_admitted_concurrent_nested_calls_share_last_slot, False, False),
        "cancel_before_forward": (test_admitted_interrupted_delivery_keeps_reservation, True, True),
        "cancel_after_forward": (test_admitted_interrupted_delivery_keeps_reservation, True, True),
    }
    assert fault in cases
    from conformance_run_fixture import activate_prepared_controller

    activate_prepared_controller(base / "controller")
    runner, patched, parameter = cases[fault]
    arguments = {
        "tmp_path": base,
        "arm": arm,
        "unified": True,
        "run_setup": {
            "base": base,
            "operation": "check",
            "reference_session": os.environ.get("AISLE_PREPARED_RUN_REFERENCE"),
            "output_name": os.environ.get("AISLE_PREPARED_RUN_OUTPUT", "session"),
        },
    }
    if parameter:
        arguments["fault"] = fault
    with pytest.MonkeyPatch.context() as monkeypatch:
        if patched:
            arguments["monkeypatch"] = monkeypatch
        runner(**arguments)
