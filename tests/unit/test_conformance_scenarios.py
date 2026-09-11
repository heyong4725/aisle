"""MON-8/MON-12/MON-13: scenario intent is bound before the frontend runs."""

import hashlib
import json

import pytest
from test_conformance_profile_binding import bound_profile

pytestmark = pytest.mark.unit


def scenario_proof(tmp_path, *, change=None):
    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    scenario = {
        "schema_version": "aisle.frontend-conformance-scenario.v1",
        "route": "native",
        "arm": "typed",
        "case": "available",
        "target": candidates["typed"]["repository"]["editable_allowlist"][0],
        "marker": "# AISLE probe bound",
        "selector": {"attempt": 2},
    }
    if change is not None:
        change(scenario)
    scenario_raw = json.dumps(scenario).encode()
    profile = json.loads((root / reference["path"]).read_bytes())
    name = "fixtures/scenario.json"
    profile["fixture_files"][name] = hashlib.sha256(scenario_raw).hexdigest()
    raw = json.dumps(profile).encode()
    reference["sha256"] = hashlib.sha256(raw).hexdigest()
    for launch in launches.values():
        launch["conformance_profile"] = reference
    artifacts = {"conformance/profile.json": raw, "conformance/inputs/" + name: scenario_raw}
    artifacts.update(
        {
            "conformance/inputs/" + path: (root / path).read_bytes()
            for path in set(profile["controller_files"]) | (set(profile["fixture_files"]) - {name})
        }
    )
    return {
        "artifacts": artifacts,
        "record": {"arm": "typed"},
        "admission": {"arms": candidates, "launch_bindings": launches},
    }, scenario


@pytest.mark.parametrize(
    "change",
    [
        None,
        lambda value: value.update(arm="monolithic"),
        lambda value: value.update(case="passed"),
        lambda value: value.update(route="invented"),
        lambda value: value.update(target="../outside"),
        lambda value: value.update(target="unlisted.yaml"),
        lambda value: value.update(marker="different"),
        lambda value: value.update(selector={"attempt": True}),
        lambda value: value.update(selector={"attempt": 0}),
        lambda value: value.update(selector={"attempt": 2, "unchecked": True}),
    ],
)
def test_scenario_parameters_are_validated_after_binding(tmp_path, change):
    """MON-13: even correctly hashed scenario bytes cannot select another arm or effect."""
    from aisle.harness.frontend_qualification import read_bound_scenario

    proof, expected = scenario_proof(tmp_path, change=change)
    if change is None:
        assert read_bound_scenario(proof) == expected
    else:
        with pytest.raises(ValueError):
            read_bound_scenario(proof)


@pytest.mark.parametrize("drift", ["scenario", "profile", "controller", "unbound"])
def test_scenario_requires_original_prelaunch_profile(tmp_path, drift):
    """MON-8/MON-13: post-hoc labels cannot replace the originally admitted fixture."""
    from aisle.harness.frontend_qualification import read_bound_scenario

    proof, _ = scenario_proof(tmp_path)
    artifacts = proof["artifacts"]
    if drift == "scenario":
        artifacts["conformance/inputs/fixtures/scenario.json"] += b" "
    elif drift == "profile":
        artifacts["conformance/profile.json"] += b" "
    elif drift == "controller":
        artifacts["conformance/inputs/src/aisle/harness/frontend_effects.py"] += b" "
    else:
        raw = json.loads(artifacts["conformance/profile.json"])
        del raw["fixture_files"]["fixtures/scenario.json"]
        artifacts["conformance/profile.json"] = json.dumps(raw).encode()
        proof["admission"]["launch_bindings"]["typed"]["conformance_profile"]["sha256"] = (
            hashlib.sha256(artifacts["conformance/profile.json"]).hexdigest()
        )
    with pytest.raises(ValueError):
        read_bound_scenario(proof)


def test_bound_scenario_alone_cannot_qualify_execution(tmp_path):
    """MON-12/MON-13: intent and a green label cannot substitute for original execution."""
    from aisle.harness.frontend_qualification import route_scenario_evidence

    proof, _ = scenario_proof(tmp_path)
    proof["record"].update(
        ok=True,
        error=None,
        classification="engineering_execution",
        process={"rc": 0, "stream_complete": True},
        postflight={"classification": "synthetic_pass"},
    )
    with pytest.raises(ValueError):
        route_scenario_evidence(proof)


@pytest.mark.parametrize(
    ("route", "selector", "valid"),
    [
        ("continued_input", {"attempt": 3, "startup_attempt": 2}, True),
        ("continued_input", {"attempt": 2, "startup_attempt": 2}, False),
        ("continued_input", {"attempt": 2, "startup_attempt": 3}, False),
        ("continued_input", {"attempt": 3, "startup_attempt": True}, False),
        ("hosted", {"request_id": "00000002"}, True),
        ("hosted", {"request_id": "00000000"}, False),
        ("hosted", {"request_id": 2}, False),
        ("hosted", {"request_id": "../../02"}, False),
        ("nested", {"attempt": 2}, True),
        ("mcp", {"attempt": 2}, True),
        ("native_edit", {"attempt": 2}, True),
        ("subagents", {"attempt": 3}, True),
    ],
)
def test_route_selectors_identify_one_ordered_bound_dispatch(tmp_path, route, selector, valid):
    """MON-13: process input must follow startup; hosted selectors must name a request."""
    from aisle.harness.frontend_qualification import read_bound_scenario

    proof, expected = scenario_proof(
        tmp_path, change=lambda value: value.update(route=route, selector=selector)
    )
    if valid:
        assert read_bound_scenario(proof) == expected
    else:
        with pytest.raises(ValueError):
            read_bound_scenario(proof)
