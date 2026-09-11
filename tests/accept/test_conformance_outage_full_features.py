"""MON-8/MON-12/MON-13: controller faults preserve the admitted frontend surface."""

import copy
import hashlib
import json

import pytest
from test_matched_conformance_session import (
    test_admitted_controller_timeout_retains_actual_frontend_request as _timeout,
)
from test_matched_conformance_session import (
    test_admitted_controller_unavailable_retains_actual_frontend_request as _unavailable,
)
from test_matched_conformance_session import (
    test_admitted_malformed_response_retains_rejected_bytes as _malformed,
)

pytestmark = pytest.mark.accept


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("fault", ["unavailable", "timeout", "malformed"])
def test_outage_with_all_frontend_features(tmp_path, monkeypatch, arm, fault):
    """MON-13: enabling unused MCP, hosted and nested tools cannot erase an owned fault."""
    {"unavailable": _unavailable, "timeout": _timeout, "malformed": _malformed}[fault](
        tmp_path, monkeypatch, arm, unified=True
    )
    _audit_retained_outage(tmp_path / "retained/session")


def _audit_retained_outage(session):
    """MON-13: retained original faults reject unrelated optional-route failures."""
    from aisle.harness.frontend_conformance import acquire_session_inputs, read_session_proof
    from aisle.harness.frontend_qualification import audit_controller_fault

    inputs = acquire_session_inputs(session)
    profile = json.loads(inputs["conformance/profile.json"])
    files = {"proof/" + name: raw for name, raw in inputs.items()}
    profile["proof_files"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    for name in (*profile["controller_files"], *profile["fixture_files"]):
        files[name] = inputs["conformance/inputs/" + name]
    record = json.loads(inputs["matched-session.json"])
    admission = json.loads(inputs["admission.json"])
    arm = record["arm"]
    proof = read_session_proof(
        {"profile": profile, "files": files},
        "proof/matched-session.json",
        candidate=admission["arms"][arm],
        launch=admission["launch_bindings"][arm],
        arm=arm,
    )
    result = audit_controller_fault(proof)
    assert result["fault_verified"] and result["controller_attempts"] == 0
    assert result["dispatch_delivery_uncertain"] and record["ok"] is False
    for fault in ("host", "proxy", "protocol", "mcp"):
        changed = copy.deepcopy(proof)
        name = {
            "host": "code-mode-reference.json",
            "proxy": "code-mode-reference.json",
            "protocol": "frontend-protocol-reference.json",
            "mcp": "mcp-harness-reference.json",
        }[fault]
        reference = json.loads(changed["artifacts"][name])
        if fault == "proxy":
            reference["proxy"]["failure"] = "unrelated proxy failure"
        elif fault == "protocol":
            reference["failure"] = {"error_type": "ValueError", "error": "unrelated failure"}
        else:
            reference["failure"] = "unrelated failure"
        changed["artifacts"][name] = json.dumps(reference).encode()
        with pytest.raises(ValueError):
            audit_controller_fault(changed)
    return result
