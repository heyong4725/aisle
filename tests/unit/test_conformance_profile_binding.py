"""MON-8/MON-13: profile identities bind actual controller, fixture and launch bytes."""

import hashlib
import json

import pytest
from test_matched_session import _app_server_launch_pair, prepared_pair

pytestmark = pytest.mark.unit


def bound_profile(tmp_path):
    from aisle.harness.frontend_conformance import ROUTE_UNITS, configuration_digest
    from aisle.harness.matched_session import CONTROLLER_FILES

    root, candidates, views = prepared_pair(tmp_path)
    launches = _app_server_launch_pair(candidates)
    fixture = root / "qualification-fixture.py"
    fixture.write_text("# engineering input, not a route qualification\n")
    profile = {
        "schema_version": "aisle.frontend-conformance.v1",
        "frontend": candidates["typed"]["agent"],
        "bindings": {
            arm: configuration_digest(candidates[arm], launch) for arm, launch in launches.items()
        },
        "controller_files": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in CONTROLLER_FILES
        },
        "fixture_files": {fixture.name: hashlib.sha256(fixture.read_bytes()).hexdigest()},
        "routes": {
            name: {"counting_unit": unit, "proofs": []} for name, unit in ROUTE_UNITS.items()
        },
    }
    path = root / "profile.json"
    path.write_text(json.dumps(profile))
    reference = {"path": "profile.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return root, candidates, launches, reference, fixture


def test_profile_binding_preserves_unqualified_route_inventory(tmp_path):
    """MON-13: matching bytes establish profile identity without inventing route evidence."""
    from aisle.harness.frontend_conformance import read_bound_profile

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    bound = read_bound_profile(
        root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
    )
    assert bound["profile_bytes"] == (root / "profile.json").read_bytes()
    assert all(row["proofs"] == [] for row in bound["profile"]["routes"].values())


def test_effect_verifier_is_part_of_the_bound_controller(tmp_path):
    """MON-13: effect interpretation cannot change without invalidating controller identity."""
    from aisle.harness.frontend_conformance import read_bound_profile

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    name = "src/aisle/harness/frontend_effects.py"
    profile = json.loads((root / reference["path"]).read_bytes())
    assert name in profile["controller_files"]
    (root / name).write_bytes((root / name).read_bytes() + b"\n# drift\n")
    with pytest.raises(ValueError):
        read_bound_profile(
            root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
        )


@pytest.mark.parametrize(
    "fault", ["fixture", "profile", "configuration", "revision", "symlink", "route_inventory"]
)
def test_profile_binding_refuses_drift_and_unbound_surface(tmp_path, fault):
    """MON-13: valid-looking profile metadata cannot hide changed bound inputs."""
    from aisle.harness.frontend_conformance import read_bound_profile

    root, candidates, launches, reference, fixture = bound_profile(tmp_path)
    if fault == "fixture":
        fixture.write_text("# changed fixture\n")
    elif fault == "profile":
        (root / "profile.json").write_text("{}")
    elif fault == "configuration":
        launches["typed"]["argv"].append("--changed")
    elif fault == "revision":
        candidates["typed"]["agent"]["cli_revision"] = "different"
    elif fault == "symlink":
        fixture.rename(root / "target.py")
        fixture.symlink_to(root / "target.py")
    else:
        profile = json.loads((root / "profile.json").read_bytes())
        del profile["routes"]["hosted"]
        (root / "profile.json").write_text(json.dumps(profile))
        reference["sha256"] = hashlib.sha256((root / "profile.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        read_bound_profile(
            root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
        )


def test_partial_profile_is_bound_at_admission_and_rechecked_on_resume(tmp_path):
    """MON-8/MON-13: engineering admission retains an explicit profile and refuses later drift."""
    from aisle.harness.matched_session import AdmissionError, admit_pair, verify_plan

    root, candidates, launches, reference, fixture = bound_profile(tmp_path)
    views = {arm: tmp_path / arm for arm in candidates}
    for launch in launches.values():
        launch["conformance_profile"] = reference
    plan = admit_pair(root, candidates, views, launches=launches)
    assert verify_plan(plan, root, views)["launch_bindings"] == launches
    fixture.write_text("# changed after admission\n")
    with pytest.raises(AdmissionError, match="conformance|profile|fixture"):
        verify_plan(plan, root, views)


def test_profile_hash_is_checked_before_reading_declared_inputs(tmp_path, monkeypatch):
    """MON-13: a changed profile cannot select additional controller reads."""
    from aisle.harness import frontend_conformance as profiles

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    profile = json.loads((root / "profile.json").read_bytes())
    profile["fixture_files"]["unbound-input"] = "0" * 64
    (root / "profile.json").write_text(json.dumps(profile))
    reads = []
    original = profiles._read

    def read(root, name, limit):
        reads.append(name)
        return original(root, name, limit)

    monkeypatch.setattr(profiles, "_read", read)
    with pytest.raises(ValueError):
        profiles.read_bound_profile(
            root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
        )
    assert reads == ["profile.json"]


@pytest.mark.parametrize("fault", [None, "launcher", "source", "retained"])
def test_session_retains_profile_for_offline_recheck(tmp_path, fault):
    """MON-12/MON-13: retain bound inputs before launch and audit them even on failure."""
    import shutil

    from aisle.harness.matched_session import admit_pair, execute_session

    root, candidates, launches, reference, fixture = bound_profile(tmp_path)
    views = {arm: tmp_path / arm for arm in candidates}
    for launch in launches.values():
        launch["conformance_profile"] = reference
    plan = admit_pair(root, candidates, views, launches=launches)
    output = tmp_path / "attempt"
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps(
            {
                "schema_version": "aisle.hidden-access-log.v1",
                "adapter_active": True,
                "complete": True,
                "events": [],
            }
        )
    )
    original = fixture.read_bytes()
    launched = []

    def launch(destination):
        launched.append(True)
        retained = destination / "conformance" / "inputs" / fixture.name
        assert retained.read_bytes() == original
        if fault == "launcher":
            raise RuntimeError("failed launch fixture")
        if fault == "source":
            fixture.write_text("# changed during execution\n")
        if fault == "retained":
            retained.write_text("# changed retained evidence\n")
        return {"rc": 0}

    result = execute_session(
        plan,
        root,
        views,
        "typed",
        output,
        session_id="profile",
        launch=launch,
        hidden_access_log=access,
    )
    assert launched
    audit = result["common_evidence"]["audits"]["conformance_profile"]
    assert audit["reference"] == reference
    assert audit["status"] == ("invalid" if fault == "retained" else "bindings_verified")
    assert audit["qualification"] == "not_evaluated"
    assert result["ok"] is (fault is None)
    if fault == "retained":
        assert "conformance" in result["error"]
    else:
        from aisle.harness.frontend_conformance import read_retained_profile

        shutil.rmtree(root)
        bound = read_retained_profile(
            output / "conformance",
            reference,
            candidate=candidates["typed"],
            launch=launches["typed"],
            arm="typed",
        )
        assert bound["files"][fixture.name] == original
        assert (
            result["artifacts"]["conformance/inputs/" + fixture.name]
            == hashlib.sha256(original).hexdigest()
        )
