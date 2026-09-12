"""MON-12/MON-13: native fault qualification preserves an unused host's failure."""

import json

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget
from aisle.harness.frontend_qualification import _authority_evidence

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault", [None, "unknown", "proxy", "bytes", "nested_charge", "unreferenced_rpc"]
)
@pytest.mark.parametrize(
    "host_failure",
    [
        "ValueError: app-server turn failed",
        "CancelledError: ",
        "DispatchRefused: dispatch budget exhausted",
    ],
)
@pytest.mark.parametrize("mode", ["unused", "refusal"])
def test_unused_host_failure_requires_empty_verified_source(tmp_path, fault, host_failure, mode):
    """MON-13: native fault evidence cannot hide host activity or unrelated failure."""
    root = tmp_path / "dispatch"
    with DispatchBudget(root, session_id="unused-host", ceiling=4) as budget:
        if fault == "nested_charge":
            budget.dispatch(
                {"turn_id": "code-mode:execution", "call_id": "call", "tool_name": "exec_command"},
                b"probe",
                lambda _: None,
            )
    artifacts = {"frontend-dispatch/" + p.name: p.read_bytes() for p in root.iterdir()}
    artifacts["frontend-dispatch-reference.json"] = json.dumps(budget.reference()).encode()
    for name in ("frontend-authority", "frontend-protocol"):
        artifacts[name + "-reference.json"] = b'{"artifacts":{}}'
    host = {
        "failure": "other failure" if fault == "unknown" else host_failure,
        "delegated_tools": [],
        "proxy": {
            "artifacts": {},
            "bytes": 1 if fault == "bytes" else 0,
            "failure": "unknown" if fault == "proxy" else None,
            "complete_coverage": False,
            "confinement_verified": False,
        },
    }
    artifacts["code-mode-reference.json"] = json.dumps(host).encode()
    if fault == "unreferenced_rpc":
        artifacts["code-mode/rpc/00000001.json"] = b"{}"
    with pytest.raises(ValueError, match="nested host did not complete"):
        _authority_evidence(artifacts)
    options = {"unused_host": True} if mode == "unused" else {"refusal": True}
    if fault is not None or (host_failure.startswith("DispatchRefused:") and mode != "refusal"):
        with pytest.raises(ValueError):
            _authority_evidence(artifacts, **options)
    else:
        authority = _authority_evidence(artifacts, **options)
        assert authority["unused_host_failure"] == host["failure"]
        assert authority["code_mode"]["artifacts"] == {}
