"""MON-13: native fault evidence preserves enabled but unused source journals."""

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault", [None, "missing", "undeclared", "nested_charge", "mcp_call", "bytes", "delegation"]
)
def test_native_fault_requires_verified_unused_routes(tmp_path, fault):
    """MON-13: real source verifiers reject activity, missing evidence, and changed delegation."""
    from aisle.harness.frontend_qualification import _native_fault_delegations

    root = tmp_path / "dispatch"
    with DispatchBudget(root, session_id="native-fault", ceiling=4) as budget:
        budget.dispatch(
            {
                "turn_id": "code-mode:execution" if fault == "nested_charge" else "provider",
                "call_id": "call",
                "tool_name": "exec_command",
            },
            b"noop",
            lambda _: None,
        )
    delegated = {
        "harness.check",
        "harness.run",
        "mcp__aisle_harness.check",
        "mcp__aisle_harness.run",
    }
    authority = {
        "dispatch": {
            "artifacts": {p.name: p.read_bytes() for p in root.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 65536,
        },
        "code_mode": {
            "artifacts": {},
            "expected": {
                "artifacts": {},
                "bytes": 0,
                "failure": None,
                "complete_coverage": False,
                "confinement_verified": False,
            },
            "delegated_tools": delegated,
            "byte_limit": 65536,
        },
        "mcp_harness": {
            "artifacts": {},
            "expected": {"artifacts": {}, "failure": None, "calls": 0},
            "byte_limit": 65536,
        },
    }
    candidate = {"policy": {"allowed_external_tools": ["harness.check", "harness.run"]}}
    launch = {"code_mode_host": {}, "mcp_harness": True}
    if fault == "missing":
        del authority["code_mode"]
    elif fault == "undeclared":
        del launch["code_mode_host"]
    elif fault == "mcp_call":
        authority["mcp_harness"]["expected"]["calls"] = 1
    elif fault == "bytes":
        authority["code_mode"]["expected"]["bytes"] = 1
    elif fault == "delegation":
        authority["code_mode"]["delegated_tools"] = {"harness.check"}
    if fault is None:
        actual = _native_fault_delegations(candidate, launch, authority)
        assert actual == {
            (namespace, name, "function_call")
            for namespace in ("harness", "mcp__aisle_harness")
            for name in ("check", "run")
        } | {(None, "exec", "custom_tool_call"), ("functions", "exec", "custom_tool_call")}
        assert _native_fault_delegations(candidate, {}, {"dispatch": authority["dispatch"]}) == {
            ("harness", "check", "function_call"),
            ("harness", "run", "function_call"),
        }
    else:
        with pytest.raises(ValueError):
            _native_fault_delegations(candidate, launch, authority)
