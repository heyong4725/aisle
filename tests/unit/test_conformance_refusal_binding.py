"""MON-8/MON-12/MON-13: source checks are bound to admitted session budgets."""

import hashlib
import json

import pytest
from test_app_server_source_audit import _case
from test_frontend_dispatch import call

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("drift", [None, "budget", "session", "plan", "unknown_source", "policy"])
def test_composed_refusal_requires_admitted_identity_and_all_sources(tmp_path, drift):
    """MON-13: a valid local journal cannot qualify another admission or an unknown route."""
    from aisle.harness.frontend_qualification import refusal_source_evidence

    def encode(row):
        return json.dumps(row).encode() + b"\n"

    records, _ = _case()
    del records["00000005-received.json"], records["00000005-sent.json"]
    records["failure.json"] = {
        "error_type": "DispatchRefused",
        "error": "dispatch budget exhausted",
    }
    protocol = {name: encode(row) for name, row in records.items()}
    protocol_ref = {
        "thread_id": "thread",
        "turn_id": "turn",
        "dynamic_calls": 1,
        "artifacts": {n: hashlib.sha256(v).hexdigest() for n, v in protocol.items()},
        "stream_complete": False,
        "failure": records["failure.json"],
    }
    root = tmp_path / "dispatch"
    with DispatchBudget(root, session_id="session", ceiling=1) as budget:
        budget.dispatch(call(1), b"earlier", lambda _: None)
        with pytest.raises(DispatchRefused):
            budget.dispatch(
                {
                    "turn_id": "turn",
                    "call_id": "call",
                    "tool_name": "unknown" if drift == "unknown_source" else "harness.check",
                },
                protocol["00000004-received.json"],
                lambda _: pytest.fail("delivered"),
            )
    artifacts = {"frontend-protocol/" + n: v for n, v in protocol.items()}
    artifacts.update({"frontend-dispatch/" + p.name: p.read_bytes() for p in root.iterdir()})
    artifacts.update(
        {
            "frontend-protocol-reference.json": encode(protocol_ref),
            "frontend-dispatch-reference.json": encode(budget.reference()),
            "frontend-authority-reference.json": encode({"session_id": "session", "artifacts": {}}),
        }
    )
    proof = {
        "artifacts": artifacts,
        "record": {
            "session_id": "other" if drift == "session" else "session",
            "arm": "typed",
            "plan_id": "plan",
        },
        "admission": {
            "immutable_id": "other" if drift == "plan" else "plan",
            "arms": {
                "typed": {
                    "budget": {"frontend_tool_ceiling": 2 if drift == "budget" else 1},
                    "policy": {
                        "allowed_external_tools": [] if drift == "policy" else ["harness.check"]
                    },
                }
            },
            "launch_bindings": {"typed": {"app_server": {}}},
        },
    }
    if drift is None:
        result = refusal_source_evidence(proof)
        assert result["linked_local_attempts"] == [2]
        assert result["complete_coverage"] is False
    else:
        with pytest.raises(ValueError):
            refusal_source_evidence(proof)
