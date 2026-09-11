"""MON-12/MON-13: session proof execution is recomputed from retained source."""

import pytest

pytestmark = pytest.mark.unit


def test_nested_delegate_names_survive_proof_snapshot_reconstruction():
    """MON-13: replay preserves named nested harness delegates as complete tool names."""
    import json

    from aisle.harness.frontend_qualification import _authority_evidence

    empty = json.dumps({"artifacts": {}}).encode()
    artifacts = {
        name: empty
        for name in (
            "frontend-authority-reference.json",
            "frontend-protocol-reference.json",
            "frontend-dispatch-reference.json",
        )
    }
    artifacts["code-mode-reference.json"] = json.dumps(
        {"failure": None, "proxy": {"artifacts": {}}, "delegated_tools": ["harness__check"]}
    ).encode()
    assert _authority_evidence(artifacts)["code_mode"]["delegated_tools"] == {"harness__check"}


@pytest.mark.parametrize(
    "claimed",
    [
        None,
        {
            "ok": True,
            "frontend_source_verified": True,
            "frontend_authorization_verified": True,
            "frontend_reservation_verified": True,
        },
    ],
)
def test_success_claim_without_owned_evidence_cannot_qualify(claimed):
    """MON-13: a recorded green audit cannot replace the original service/source records."""
    from aisle.harness.frontend_qualification import audit_proof_execution

    proof = {
        "record": {
            "session_id": "session",
            "plan_id": "plan",
            "arm": "typed",
            "ok": True,
            "process": {"rc": 0},
            "tool_audit": claimed,
        },
        "admission": {"arms": {"typed": {"budget": {"frontend_tool_ceiling": 3}}}},
        "artifacts": {},
    }
    with pytest.raises(ValueError, match="evidence|source|artifact"):
        audit_proof_execution(proof)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a/../../outside"])
def test_proof_materialization_cannot_escape_its_private_directory(name):
    """MON-13: acquired artifact names never select writes outside the replay directory."""
    from aisle.harness.frontend_qualification import audit_proof_execution

    with pytest.raises(ValueError):
        audit_proof_execution({"record": {}, "admission": {}, "artifacts": {name: b"no"}})


@pytest.mark.parametrize(
    "change",
    [
        {"ok": False},
        {"error": "source drift"},
        {"classification": "infrastructure_exclusion"},
        {"process": {"rc": 1, "stream_complete": True}},
        {"process": {"rc": False, "stream_complete": True}},
        {"process": {"rc": 0, "stream_complete": False}},
        {"process": {"rc": 0, "stream_complete": True, "stopped": "budget"}},
        {"postflight": {"classification": "infrastructure_exclusion"}},
    ],
)
def test_invalid_session_cannot_supply_availability_evidence(change):
    """MON-13: route availability cannot be inferred from a failed, truncated or changed session."""
    from aisle.harness.frontend_qualification import available_route_evidence

    record = {
        "ok": True,
        "error": None,
        "classification": "engineering_execution",
        "process": {"rc": 0, "stream_complete": True},
        "postflight": {"classification": "synthetic_pass"},
        **change,
    }
    with pytest.raises(ValueError, match="completed engineering session"):
        available_route_evidence({"record": record})


@pytest.mark.parametrize("stopped", [None, "agent_done"])
def test_normal_frontend_completion_is_an_eligible_availability_session(stopped):
    """MON-12: the adapter's normal agent_done status must not be mistaken for a budget stop."""
    from aisle.harness.frontend_qualification import _require_available_session

    _require_available_session(
        {
            "ok": True,
            "error": None,
            "classification": "engineering_execution",
            "process": {"rc": 0, "stream_complete": True, "stopped": stopped},
            "postflight": {"classification": "synthetic_pass"},
        }
    )


@pytest.mark.parametrize("entry", ["audit_proof_execution", "refusal_source_evidence"])
@pytest.mark.parametrize("case", ["valid", "file", "session", "count"])
def test_qualification_distinguishes_file_and_session_bounds(monkeypatch, entry, case):
    """MON-12/MON-13: replay admits bounded multi-file sessions, never oversized files or
    inventories.
    """
    from aisle.harness import frontend_qualification as qualification

    monkeypatch.setattr(qualification, "MAX_INPUT_BYTES", 8)
    monkeypatch.setattr(qualification, "MAX_SESSION_BYTES", 24, raising=False)
    monkeypatch.setattr(qualification, "MAX_SESSION_FILES", 4, raising=False)
    artifacts = {"a": b"12345678", "b": b"12345678"}
    if case == "file":
        artifacts = {"a": b"123456789"}
    elif case == "session":
        artifacts = {str(i): b"12345678" for i in range(4)}
    elif case == "count":
        artifacts = {str(i): b"" for i in range(5)}

    def reached_authority(*args, **kwargs):
        raise RuntimeError("bounds accepted; semantic audit still required")

    monkeypatch.setattr(qualification, "_authority_evidence", reached_authority)
    expected = RuntimeError if case == "valid" else ValueError
    message = "bounds accepted" if case == "valid" else "unbounded"
    with pytest.raises(expected, match=message):
        getattr(qualification, entry)({"artifacts": artifacts, "record": {}, "admission": {}})
