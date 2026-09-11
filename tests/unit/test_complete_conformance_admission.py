"""MON-8/MON-13: complete admission requires both replay gates on every recheck."""

import hashlib
import json

import pytest
from test_conformance_profile_binding import bound_profile

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "phase,failed_gate",
    [
        (None, None),
        ("admit", "routes"),
        ("admit", "faults"),
        ("resume", "routes"),
        ("resume", "faults"),
        ("active", "routes"),
        ("active", "faults"),
    ],
)
def test_complete_policy_obeys_both_gates_on_admission_and_recheck(
    tmp_path, monkeypatch, phase, failed_gate
):
    """MON-13: compositional gate test; mocked verdicts do not qualify actual coverage."""
    from aisle.harness import frontend_conformance as profiles
    from aisle.harness.matched_session import (
        AdmissionError,
        admit_pair,
        verify_active_plan,
        verify_plan,
    )

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    views = {arm: tmp_path / arm for arm in candidates}
    for arm in candidates:
        candidates[arm]["policy"]["allowed_external_tools"].append("harness.check")
        candidates[arm]["budget"].update(frontend_coverage="complete", frontend_tool_ceiling=3)
        launches[arm]["conformance_profile"] = reference
    path = root / reference["path"]
    profile = json.loads(path.read_bytes())
    profile["execution_sha256"] = profiles.execution_digest({})
    profile["bindings"] = {
        arm: profiles.configuration_digest(candidates[arm], launches[arm]) for arm in candidates
    }
    path.write_text(json.dumps(profile))
    reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []
    rejecting = phase == "admit"

    def gate(name):
        def verify(bound, *, candidate, launch, arm):
            calls.append((name, arm))
            if rejecting and name == failed_gate:
                raise ValueError("unverified " + name + " evidence")
            return {("routes_verified" if name == "routes" else "adapter_faults_verified"): True}

        return verify

    monkeypatch.setattr(profiles, "verify_route_matrix", gate("routes"))
    monkeypatch.setattr(profiles, "verify_fault_matrix", gate("faults"))
    if phase == "admit":
        with pytest.raises(AdmissionError, match="unverified " + failed_gate):
            admit_pair(root, candidates, views, launches=launches)
        return
    plan = admit_pair(root, candidates, views, launches=launches)
    assert plan["confirmatory_ready"] is False
    assert set(calls) == {(gate, arm) for gate in ("routes", "faults") for arm in candidates}
    calls.clear()
    rejecting = phase is not None
    if phase is None:
        verify_plan(plan, root, views)
        verify_active_plan(plan, root, views, "typed")
        assert len(calls) == 8
        assert set(calls) == {(gate, arm) for gate in ("routes", "faults") for arm in candidates}
    else:
        with pytest.raises(AdmissionError, match="unverified " + failed_gate):
            if phase == "resume":
                verify_plan(plan, root, views)
            else:
                verify_active_plan(plan, root, views, "typed")
