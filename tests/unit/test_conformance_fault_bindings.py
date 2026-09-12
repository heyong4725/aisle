"""MON-13: fault proofs may change only their explicitly declared launch input."""

import copy
import hashlib
import json

import pytest
from test_conformance_profile_binding import bound_profile

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault",
    [
        "controller_unavailable",
        "controller_timeout",
        "malformed_response",
        "denial",
        "hook_absent",
        "hook_changed",
        "provider_replay",
        "concurrent_nested",
        "cancel_before_forward",
        "cancel_after_forward",
    ],
)
def test_fault_launch_preserves_nominal_binding(fault):
    """MON-13: hook faults preserve all other admitted arguments and provider settings."""
    from aisle.harness.frontend_conformance import fault_launch

    nominal = {
        "argv": ["/codex", "app-server", "--listen", "stdio://", "-c", "features.multi_agent=true"],
        "provider": {"base_url": "http://127.0.0.1:32123/v1"},
        "app_server": {"baseInstructions": "bound"},
    }
    before = copy.deepcopy(nominal)
    actual = fault_launch(nominal, fault)
    assert nominal == before
    assert {k: v for k, v in actual.items() if k != "argv"} == {
        k: v for k, v in before.items() if k != "argv"
    }
    if fault == "hook_absent":
        assert actual["argv"] == before["argv"] + ["-c", "features.hooks=false"]
    elif fault == "hook_changed":
        assert actual["argv"][0] == before["argv"][0]
        assert actual["argv"][1] == "--dangerously-bypass-hook-trust"
        assert actual["argv"][2 : len(before["argv"]) + 1] == before["argv"][1:]
        assert actual["argv"][-4:] == [
            "-c",
            "features.hooks=true",
            "-c",
            (
                'hooks.PreToolUse=[{matcher="Bash",hooks=[{typ'
                'e="command",timeout=1,command="exit 1"}]}]'
            ),
        ]
    else:
        assert actual == before


def test_unbound_fault_proof_is_rejected_during_profile_acquisition(tmp_path):
    """MON-13: fault declarations must resolve to original, profile-bound session bytes."""
    from aisle.harness.frontend_conformance import FAULT_CASES, read_bound_profile

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    path = root / reference["path"]
    profile = json.loads(path.read_bytes())
    profile["faults"] = {name: [] for name in FAULT_CASES}
    profile["faults"]["denial"] = [{"arm": "typed", "session": "missing/matched-session.json"}]
    path.write_text(json.dumps(profile))
    reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="unbound.*proof"):
        read_bound_profile(
            root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
        )


@pytest.mark.parametrize("drift", [None, "missing", "duplicate", "label", "audit"])
def test_fault_matrix_requires_each_replayed_case(monkeypatch, drift):
    """MON-13: compositional test only; mocked case verdicts do not attest actual frontend
    coverage.
    """
    from aisle.harness import frontend_conformance as profiles
    from aisle.harness import frontend_qualification as qualification

    faults = {name: [{"arm": "typed", "session": name}] for name in profiles.FAULT_CASES}
    if drift == "missing":
        faults["denial"] = []
    elif drift == "duplicate":
        faults["denial"] *= 2

    def read(bound, name, **kwargs):
        return {"record": {"session_id": name}, "fault": name}

    def audit(proof):
        if drift == "audit":
            raise ValueError("fault source changed")
        return {"fault_verified": True, "fault": "wrong" if drift == "label" else proof["fault"]}

    monkeypatch.setattr(profiles, "read_session_proof", read)
    for name in (
        "audit_controller_fault",
        "audit_controller_denial",
        "audit_hook_independence",
        "audit_provider_replay",
        "audit_concurrent_nested",
        "audit_provider_interruption",
    ):
        monkeypatch.setattr(qualification, name, audit)
    args = ({"profile": {"faults": faults}},)
    kwargs = {"candidate": {}, "launch": {"argv": ["/codex", "app-server"]}, "arm": "typed"}
    if drift is None:
        result = profiles.verify_fault_matrix(*args, **kwargs)
        assert set(result["faults"]) == set(profiles.FAULT_CASES)
        assert result["adapter_faults_verified"] is True and result["complete_coverage"] is False
    else:
        with pytest.raises(ValueError):
            profiles.verify_fault_matrix(*args, **kwargs)


def test_fault_matrix_requires_replay_and_concurrency_inventory():
    """MON-8/MON-13: new fault auditors must be required by complete-profile qualification."""
    from aisle.harness.frontend_conformance import FAULT_CASES

    assert {
        "provider_replay",
        "concurrent_nested",
        "cancel_before_forward",
        "cancel_after_forward",
    } <= set(FAULT_CASES)
