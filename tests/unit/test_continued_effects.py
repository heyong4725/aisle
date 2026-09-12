"""MON-12/MON-13: continued input binds an owned process and exact deliverable effect."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case
from test_provider_response_authority import frames, item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("refused", [False, True])
@pytest.mark.parametrize(
    "drift",
    [
        None,
        "failed_terminal",
        "process",
        "command",
        "input",
        "interaction",
        "session_float",
        "login_integer",
    ],
)
def test_continued_effect_requires_exact_process_and_input(tmp_path, refused, drift):
    """MON-13: another process, different input, or unexpected delivery cannot prove the effect."""
    from aisle.harness.frontend_effects import continued_effect_evidence, continued_probe

    target, marker = "task.yaml", "# AISLE probe continued"
    command = continued_probe(target)
    startup = item(1)
    startup["arguments"] = json.dumps(
        {"cmd": "true" if drift == "command" else command, "login": False, "tty": True}
    )
    interaction = item(2, name="write_stdin")
    interaction["arguments"] = json.dumps(
        {
            "session_id": 99 if drift == "process" else 42,
            "chars": "different\n" if drift == "input" else marker + "\n",
        }
    )
    if drift == "session_float":
        interaction["arguments"] = json.dumps({"session_id": 42.0, "chars": marker + "\n"})
    elif drift == "login_integer":
        startup["arguments"] = json.dumps({"cmd": command, "login": 0, "tty": True})
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1 if refused else 2) as budget:
        authority = ProviderResponseAuthority(budget)
        authority.forward(frames([startup], response_id="start"), lambda _: None)
        if refused:
            with pytest.raises(DispatchRefused):
                authority.forward(frames([interaction], response_id="input"), lambda _: None)
        else:
            authority.forward(frames([interaction], response_id="input"), lambda _: None)
    records, _ = _case()
    del (
        records["00000004-received.json"],
        records["00000005-received.json"],
        records["00000005-sent.json"],
    )
    records["00000004-received.json"] = {
        "method": "item/started",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "item": {
                "type": "commandExecution",
                "id": "call-1",
                "processId": "42",
                "status": "inProgress",
                "commandActions": [{"type": "unknown", "command": command}],
            },
        },
    }
    if not refused or drift == "interaction":
        records["00000005-received.json"] = {
            "method": "item/commandExecution/terminalInteraction",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "itemId": "call-1",
                "processId": "42",
                "stdin": "wrong\n" if drift == "interaction" and not refused else marker + "\n",
            },
        }
    failure = {"error_type": "ValueError", "error": "fixture end"}
    if drift == "failed_terminal":
        failure = {"error_type": "ValueError", "error": "app-server turn failed"}
        records[f"{5 if refused else 6:08d}-received.json"] = {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turn": {"id": "turn", "status": "failed"},
            },
        }
    records["failure.json"] = failure
    protocol = {n: json.dumps(v).encode() + b"\n" for n, v in records.items()}
    before = b"nodes: []\n"
    after = before if refused else before + marker.encode() + b"\n"
    artifacts = {
        "authored/" + target: before,
        "final/" + target: after,
        "frontend-dispatch-reference.json": json.dumps(budget.reference()).encode(),
        "frontend-protocol-reference.json": json.dumps(
            {
                "thread_id": "thread",
                "turn_id": "turn",
                "dynamic_calls": 0,
                "stream_complete": False,
                "failure": failure,
                "artifacts": {n: hashlib.sha256(v).hexdigest() for n, v in protocol.items()},
            }
        ).encode(),
    }
    artifacts.update({"frontend-dispatch/" + p.name: p.read_bytes() for p in output.iterdir()})
    artifacts.update({"frontend-protocol/" + n: v for n, v in protocol.items()})
    proof = {
        "artifacts": artifacts,
        "record": {
            "arm": "typed",
            "snapshots": {
                phase: {target: {"sha256": hashlib.sha256(raw).hexdigest(), "mode": 0o644}}
                for phase, raw in (("authored", before), ("final", after))
            },
        },
        "admission": {"arms": {"typed": {"repository": {"editable_allowlist": [target]}}}},
    }
    args = dict(attempt=2, startup_attempt=1, target=target, marker=marker, refused=refused)
    if drift in {None, "failed_terminal"}:
        assert continued_effect_evidence(proof, **args)["effect"] == (
            "unchanged" if refused else "appended"
        )
    else:
        with pytest.raises(ValueError):
            continued_effect_evidence(proof, **args)
