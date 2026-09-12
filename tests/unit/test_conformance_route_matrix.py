"""MON-8/MON-13: route qualification composes verified cases without relaxing bindings."""

import pytest

from aisle.harness.frontend_conformance import ROUTE_UNITS

pytestmark = pytest.mark.unit


def test_complete_admission_replays_bound_route_inventory(tmp_path):
    """MON-8/MON-13: byte-valid profiles with no route cases cannot admit complete coverage."""
    from test_conformance_profile_binding import bound_profile

    from aisle.harness.matched_session import AdmissionError, admit_pair

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    for arm, launch in launches.items():
        launch["conformance_profile"] = reference
        candidates[arm]["policy"]["allowed_external_tools"].append("harness.check")
        candidates[arm]["budget"]["frontend_coverage"] = "complete"
        candidates[arm]["budget"]["frontend_tool_ceiling"] = 3
    # Bind the requested budget before admission, without inventing any proof.
    import hashlib
    import json

    from aisle.harness.frontend_conformance import configuration_digest, execution_digest

    path = root / reference["path"]
    profile = json.loads(path.read_bytes())
    profile["execution_sha256"] = execution_digest({})
    profile["bindings"] = {
        arm: configuration_digest(candidates[arm], launch) for arm, launch in launches.items()
    }
    path.write_text(json.dumps(profile))
    reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(AdmissionError, match="unverified route cases"):
        admit_pair(root, candidates, {arm: tmp_path / arm for arm in candidates}, launches=launches)


@pytest.mark.parametrize(
    "fault",
    [None, "missing", "route", "duplicate", "launch", "case", "binding", "missing_run", "denial"],
)
def test_route_matrix_requires_each_case_and_original_launch(monkeypatch, fault):
    """MON-13: composition propagates source/launch failures and requires both outcomes.

    Semantic verifiers are isolated here; this test does not attest a frontend.
    Actual retained-session replay separately exercises the composed boundary.
    """
    from aisle.harness import frontend_conformance as profiles
    from aisle.harness import frontend_qualification as qualification

    matrix = {}
    proofs = {}
    for route, unit in ROUTE_UNITS.items():
        rows = []
        for case in ("available", "quota_refused"):
            name = f"{route}/{case}/matched-session.json"
            rows.append({"arm": "typed", "session": name})
            proofs[name] = {"record": {"session_id": name}, "route": route, "case": case}
        matrix[route] = {"counting_unit": unit, "proofs": rows}
    if fault == "missing":
        matrix["hosted"]["proofs"].pop()
    elif fault == "duplicate":
        matrix["hosted"]["proofs"].append(matrix["hosted"]["proofs"][0])
    elif fault == "route":
        proofs["hosted/available/matched-session.json"]["route"] = "native"
    seen = []

    def read(bound, name, *, candidate, launch, arm):
        assert candidate == expected_candidate and launch == {"launch": True} and arm == "typed"
        if fault == "binding":
            raise ValueError("different configuration")
        return proofs[name]

    def original(proof):
        if fault == "launch":
            raise ValueError("unowned listener")
        seen.append(proof["record"]["session_id"])
        return {"launch_verified": True, "confinement_verified": False}

    def scenario(proof):
        if fault == "case":
            raise ValueError("effect not verified")
        return {
            "route": proof["route"],
            "case": proof["case"],
            "case_verified": True,
            "operation": "check",
            "effect": {"ok": fault != "denial"},
        }

    monkeypatch.setattr(profiles, "read_session_proof", read)
    monkeypatch.setattr(qualification, "verify_original_launch", original)
    monkeypatch.setattr(qualification, "route_scenario_evidence", scenario)
    args = ({"profile": {"routes": matrix}},)
    expected_candidate = {
        "policy": {
            "allowed_external_tools": ["harness.check"]
            + (["harness.run"] if fault == "missing_run" else [])
        }
    }
    kwargs = {"candidate": expected_candidate, "launch": {"launch": True}, "arm": "typed"}
    if fault is not None:
        with pytest.raises(ValueError):
            profiles.verify_route_matrix(*args, **kwargs)
    else:
        report = profiles.verify_route_matrix(*args, **kwargs)
        assert len(seen) == 16 and len(set(seen)) == 16
        assert report["routes_verified"] is True
        assert report["complete_coverage"] is False


def test_route_matrix_requires_both_outcomes_for_each_admitted_harness_operation(monkeypatch):
    """MON-8/MON-13: check and run have independent success/refusal proof obligations."""
    from aisle.harness import frontend_conformance as profiles
    from aisle.harness import frontend_qualification as qualification

    matrix, proofs = {}, {}
    for route, unit in ROUTE_UNITS.items():
        entries = []
        for operation in ("check", "run") if route == "harness" else (None,):
            for case in ("available", "quota_refused"):
                name = f"{route}/{operation}/{case}"
                entries.append({"arm": "typed", "session": name})
                proofs[name] = {
                    "record": {"session_id": name},
                    "route": route,
                    "case": case,
                    "operation": operation,
                    "case_verified": True,
                    "effect": {"ok": True},
                }
        matrix[route] = {"counting_unit": unit, "proofs": entries}
    monkeypatch.setattr(profiles, "read_session_proof", lambda bound, name, **kwargs: proofs[name])
    monkeypatch.setattr(
        qualification, "verify_original_launch", lambda proof: {"launch_verified": True}
    )
    monkeypatch.setattr(qualification, "route_scenario_evidence", lambda proof: proof)
    candidate = {"policy": {"allowed_external_tools": ["harness.check", "harness.run"]}}
    bound = {"profile": {"routes": matrix}}
    result = profiles.verify_route_matrix(bound, candidate=candidate, launch={}, arm="typed")
    assert result["harness_operations"] == ["check", "run"]
    matrix["harness"]["proofs"].pop()
    with pytest.raises(ValueError, match="harness/run/quota_refused"):
        profiles.verify_route_matrix(bound, candidate=candidate, launch={}, arm="typed")
