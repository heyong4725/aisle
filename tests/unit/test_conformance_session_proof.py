"""MON-8/MON-12/MON-13: qualification consumes bound session records, not verdict labels."""

import hashlib
import json

import pytest
from test_conformance_profile_binding import bound_profile

from aisle.harness.matched_session import admit_pair, execute_session

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault", [None, "configuration", "artifact", "snapshot", "identity", "unbound", "execution"]
)
def test_proof_acquisition_binds_real_session_record_and_original_admission(tmp_path, fault):
    """MON-13: a bound receipt must agree with admission, retained files and editable snapshots."""
    from aisle.harness.frontend_conformance import execution_digest, read_session_proof

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    views = {arm: tmp_path / arm for arm in candidates}
    for launch in launches.values():
        launch["conformance_profile"] = reference
    plan = admit_pair(root, candidates, views, launches=launches)
    output = tmp_path / "session"
    record = execute_session(
        plan,
        root,
        views,
        "typed",
        output,
        session_id="qualification-fixture",
        launch=lambda _: {"rc": 0},
        hidden_access_log=tmp_path / "absent",
    )
    # This intentionally incomplete session establishes input acquisition only.
    # Its lack of an owned frontend must not be turned into a route qualification.
    profile = json.loads((root / "profile.json").read_bytes())
    profile["execution_sha256"] = execution_digest(plan)
    files = {
        name: (root / name).read_bytes()
        for name in set(profile["controller_files"]) | set(profile["fixture_files"])
    }
    proof_files = {
        "proof/" + str(path.relative_to(output)): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    profile["proof_files"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in proof_files.items()
    }
    files.update(proof_files)
    name = "proof/matched-session.json"
    if fault == "configuration":
        launches["typed"]["argv"].append("--changed")
    elif fault == "artifact":
        files["proof/admission.json"] += b" "
    elif fault == "snapshot":
        snapshot = next(key for key in files if key.startswith("proof/authored/"))
        files[snapshot] += b"changed"
        # Updating the profile hash cannot erase the session's original snapshot hash.
        profile["proof_files"][snapshot] = hashlib.sha256(files[snapshot]).hexdigest()
    elif fault == "identity":
        altered = dict(record, session_id="substitute")
        files[name] = json.dumps(altered).encode()
        profile["proof_files"][name] = hashlib.sha256(files[name]).hexdigest()
    elif fault == "unbound":
        del profile["proof_files"][name]
    elif fault == "execution":
        profile["execution_sha256"] = execution_digest(
            {**plan, "run_controller": {"different": True}}
        )
    bound = {"profile": profile, "files": files}
    if fault is not None:
        with pytest.raises(ValueError):
            read_session_proof(
                bound, name, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
            )
    else:
        from aisle.harness.frontend_conformance import verify_profile_inputs

        profile["routes"]["harness"]["proofs"] = [{"arm": "typed", "session": name}]
        raw = json.dumps(profile).encode()
        verified = verify_profile_inputs(
            raw,
            files,
            reference={"path": "profile.json", "sha256": hashlib.sha256(raw).hexdigest()},
            candidate=candidates["typed"],
            launch=launches["typed"],
            arm="typed",
        )
        assert verified == profile
        proof = read_session_proof(
            bound, name, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
        )
        assert proof["record"] == record
        assert proof["admission"] == plan
        assert proof["record"]["tool_audit"] is None
        assert "qualified" not in proof


def test_profile_binding_cannot_accept_a_proof_label_without_session_bytes(tmp_path):
    """MON-13: a route's proof declaration must resolve to bound session evidence."""
    from aisle.harness.frontend_conformance import read_bound_profile

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    path = root / reference["path"]
    profile = json.loads(path.read_bytes())
    profile["routes"]["native"]["proofs"] = [
        {"arm": "typed", "session": "missing/matched-session.json"}
    ]
    path.write_text(json.dumps(profile))
    reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="proof|session"):
        read_bound_profile(
            root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
        )


@pytest.mark.parametrize("changed", [False, True])
def test_failure_before_first_attempt_retains_owned_source_snapshot(tmp_path, changed):
    """MON-12/MON-13: pre-dispatch failures retain authentic source bytes without a tool journal."""
    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    views = {arm: tmp_path / arm for arm in candidates}
    for launch in launches.values():
        launch["conformance_profile"] = reference
    plan = admit_pair(root, candidates, views, launches=launches)
    output = tmp_path / "session"
    raw = b'{"error_type":"ValueError","error":"frontend unavailable"}\n'
    acquired = []

    def launch(_):
        (output / "frontend-protocol").mkdir()
        (output / "frontend-protocol/failure.json").write_bytes(raw + (b" " if changed else b""))
        raise ValueError("frontend unavailable")

    def evidence():
        acquired.append(True)
        return {"artifacts": {}, "protocol": {"artifacts": {"failure.json": raw}}}

    record = execute_session(
        plan,
        root,
        views,
        "typed",
        output,
        session_id="qualification-fixture",
        launch=launch,
        hidden_access_log=tmp_path / "absent",
        request_authority_evidence=evidence,
    )
    assert acquired == [True]
    assert record["ok"] is False
    assert record["tool_audit"] is None
    assert not (output / "tool-events.jsonl").exists()
    if changed:
        assert "frontend-protocol/failure.json" not in record["artifacts"]
        assert "authority evidence changed" in record["error"]
    else:
        assert (
            record["artifacts"]["frontend-protocol/failure.json"] == hashlib.sha256(raw).hexdigest()
        )
