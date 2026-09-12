"""MON-8/MON-12/MON-13: exact command effects bind admitted deliverable snapshots."""

import hashlib
import json

import pytest
from test_frontend_dispatch import call
from test_provider_response_authority import frames, item

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("refused", [False, True])
@pytest.mark.parametrize("route", ["provider", "nested", "edit", "mcp"])
@pytest.mark.parametrize(
    "drift", [None, "command", "effect", "snapshot", "target", "mode", "mode_type"]
)
def test_command_effect_binds_source_and_before_after_bytes(tmp_path, refused, drift, route):
    """MON-13: a green command or unchanged unrelated snapshot cannot prove the declared effect."""
    from aisle.harness.frontend_effects import command_effect_evidence

    verify = command_effect_evidence
    target, marker = "graphs/task.yaml", "# AISLE probe native"
    command = "printf '%s\\n' '# AISLE probe native' >> graphs/task.yaml"
    value = item(9)
    value["arguments"] = json.dumps(
        {"cmd": "true" if drift == "command" else command, "login": False}
    )
    if route == "edit":
        from aisle.harness.frontend_effects import edit_effect_evidence

        verify = edit_effect_evidence
        value = item(9, kind="custom_tool_call", name="apply_patch")
        value["input"] = (
            "invalid patch"
            if drift == "command"
            else (
                "*** Begin Patch\n*** Update File: graphs/task."
                "yaml\n@@\n nodes: []\n+# AISLE probe native\n*** "
                "End of File\n*** End Patch\n"
            )
        )
    elif route == "mcp":
        from aisle.harness.frontend_effects import mcp_effect_evidence

        verify = mcp_effect_evidence
        value = item(9, namespace="mcp__aisle_fixture", name="append")
        value["arguments"] = json.dumps(
            {"target": "other.yaml" if drift == "command" else target, "marker": marker}
        )
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        if refused:
            budget.dispatch(call(1), b"prior", lambda _: None)
        if route == "nested":
            from test_code_mode_authority import _authority, _callback

            authority = _authority(budget)
            source = _callback(input_json=value["arguments"].encode())
            forward = authority.callback
        else:
            authority = ProviderResponseAuthority(budget)
            source, forward = frames([value]), authority.forward
        if refused:
            delivered = []
            with pytest.raises(DispatchRefused):
                forward(source, delivered.append)
            assert b'"call-9"' not in b"".join(delivered)
        else:
            forward(source, lambda _: None)
    before = b"nodes: []\n"
    after = before if refused else before + marker.encode() + b"\n"
    if drift == "effect":
        after += b"unexpected\n"
    snapshots = {
        phase: {target: {"sha256": hashlib.sha256(raw).hexdigest(), "mode": 0o644}}
        for phase, raw in (("authored", before), ("final", after))
    }
    artifacts = {
        "authored/" + target: before,
        "final/" + target: after,
        "frontend-dispatch-reference.json": json.dumps(budget.reference()).encode(),
    }
    artifacts.update({"frontend-dispatch/" + p.name: p.read_bytes() for p in output.iterdir()})
    if drift == "snapshot":
        artifacts["final/" + target] += b"tampered"
    elif drift == "mode":
        snapshots["final"][target]["mode"] = 0o755
    elif drift == "mode_type":
        for phase in snapshots:
            snapshots[phase][target]["mode"] = True
    proof = {
        "artifacts": artifacts,
        "record": {"arm": "typed", "snapshots": snapshots},
        "admission": {
            "arms": {
                "typed": {
                    "repository": {"editable_allowlist": [] if drift == "target" else [target]}
                }
            }
        },
    }
    kwargs = dict(attempt=2 if refused else 1, target=target, marker=marker, refused=refused)
    if drift is None:
        result = verify(proof, **kwargs)
        assert result["effect"] == ("unchanged" if refused else "appended")
        assert result["complete_coverage"] is False
    else:
        with pytest.raises(ValueError):
            verify(proof, **kwargs)


@pytest.mark.parametrize("target", ["task.yaml", "task with spaces.yaml"])
def test_command_probe_performs_only_the_literal_append(tmp_path, target):
    """MON-12: effect probe quoting keeps marker text literal during actual shell execution."""
    import subprocess

    from aisle.harness.frontend_effects import command_probe

    marker = "# AISLE probe $(touch unexpected) 'quoted'"
    before = b"nodes: []\n"
    (tmp_path / target).write_bytes(before)
    result = subprocess.run(
        ["/bin/sh", "-c", command_probe(target, marker)],
        cwd=tmp_path,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / target).read_bytes() == before + marker.encode() + b"\n"
    assert not (tmp_path / "unexpected").exists()
