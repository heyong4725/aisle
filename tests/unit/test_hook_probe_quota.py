"""MON-13: hook scenario selection follows the admitted dispatch quota."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ceiling", [2, 4])
@pytest.mark.parametrize("kind", ["hook_absent", "hook_changed"])
@pytest.mark.parametrize("drift", [False, True])
def test_hook_audit_selects_admitted_excess_attempt(monkeypatch, ceiling, kind, drift):
    """MON-13: compositional selector test; subsequent source checks remain required."""
    from aisle.harness import frontend_conformance as profiles
    from aisle.harness import frontend_qualification as audit

    attempt = ceiling + (2 if drift else 1)
    fault = {
        "schema_version": "aisle.frontend-conformance-fault.v1",
        "arm": "typed",
        "fault": kind,
        "route": "native",
        "attempt": attempt,
    }
    proof = {
        "record": {"arm": "typed"},
        "artifacts": {
            "conformance/profile.json": json.dumps(
                {"fixture_files": {"fixtures/fault.json": "bound"}}
            ).encode(),
            "conformance/inputs/fixtures/fault.json": json.dumps(fault).encode(),
        },
        "admission": {
            "arms": {"typed": {"budget": {"frontend_tool_ceiling": ceiling}}},
            "launch_bindings": {
                "typed": {"argv": ["/codex", "app-server", "-c", "features.hooks=false"]}
            },
        },
    }
    monkeypatch.setattr(audit, "verify_original_launch", lambda _: {})
    monkeypatch.setattr(
        audit,
        "route_scenario_evidence",
        lambda _: {
            "route": "native",
            "case": "quota_refused",
            "selector": {"attempt": attempt},
            "effect": {"effect": "unchanged"},
        },
    )

    def stop_at_launch(*args):
        raise RuntimeError("subsequent launch verification reached")

    monkeypatch.setattr(profiles, "fault_launch", stop_at_launch)
    if drift:
        with pytest.raises(ValueError, match="excess-call refusal"):
            audit.audit_hook_independence(proof)
    else:
        with pytest.raises(RuntimeError, match="subsequent launch verification"):
            audit.audit_hook_independence(proof)
