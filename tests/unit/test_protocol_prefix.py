"""MON-12/MON-13: shared protocol replay preserves ownership in failed sessions."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize(
    "drift", [None, "foreign_item", "missing_initialize", "failure_receipt", "count"]
)
def test_protocol_prefix_checks_owned_identity_without_claiming_quota(tmp_path, failed, drift):
    """MON-13: a valid failed prefix is identity evidence, never a quota decision by itself."""
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix

    records, _ = _case()
    expected = {"thread_id": "thread", "turn_id": "turn", "dynamic_calls": 1}
    if failed:
        del records["00000005-received.json"]
        records["failure.json"] = {"error_type": "ValueError", "error": "provider failed"}
        expected.update(stream_complete=False, failure=dict(records["failure.json"]))
    if drift == "foreign_item":
        records["00000005-received.json"] = {
            "method": "item/started",
            "params": {
                "threadId": "foreign",
                "turnId": "turn",
                "item": {"type": "commandExecution", "id": "native"},
            },
        }
    elif drift == "missing_initialize":
        records["00000001-received.json"] = {"method": "notice", "params": {}}
    elif drift == "failure_receipt":
        records["failure.json"] = {"error_type": "OSError", "error": "different"}
    elif drift == "count":
        expected["dynamic_calls"] = True
    artifacts = {name: json.dumps(row).encode() + b"\n" for name, row in records.items()}
    expected["artifacts"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()
    }
    if drift is None:
        prefix = read_protocol_prefix(artifacts, expected=expected, byte_limit=100000)
        assert prefix["scope"].complete is (not failed)
        assert len(prefix["calls"]) == 1
    else:
        with pytest.raises(ValueError):
            read_protocol_prefix(artifacts, expected=expected, byte_limit=100000)


@pytest.mark.parametrize("drift", [None, "thread", "turn", "status", "continued", "receipt"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_turn_is_retained_as_a_terminal_owned_prefix(drift, cancelled):
    """MON-12/MON-13: a quota-triggered frontend failure preserves its owned terminal frame."""
    from aisle.harness.frontend_app_server_audit import read_protocol_prefix

    records, _ = _case()
    terminal = {
        "method": "turn/completed",
        "params": {"threadId": "thread", "turn": {"id": "turn", "status": "failed"}},
    }
    records["00000005-received.json"] = terminal
    failure = (
        {"error_type": "CancelledError", "error": ""}
        if cancelled
        else {"error_type": "ValueError", "error": "app-server turn failed"}
    )
    records["failure.json"] = dict(failure)
    if drift == "thread":
        terminal["params"]["threadId"] = "foreign"
    elif drift == "turn":
        terminal["params"]["turn"]["id"] = "foreign"
    elif drift == "status":
        terminal["params"]["turn"]["status"] = "unknown"
    elif drift == "continued":
        records["00000006-received.json"] = {"method": "notice", "params": {}}
    elif drift == "receipt":
        failure["error"] = records["failure.json"]["error"] = "unrelated failure"
    artifacts = {name: json.dumps(row).encode() + b"\n" for name, row in records.items()}
    expected = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 1,
        "stream_complete": False,
        "failure": failure,
        "artifacts": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
    }
    if drift is None:
        prefix = read_protocol_prefix(artifacts, expected=expected, byte_limit=100000)
        assert prefix["scope"].complete is False
        assert prefix["failed_turn"] == terminal
    else:
        with pytest.raises(ValueError):
            read_protocol_prefix(artifacts, expected=expected, byte_limit=100000)
