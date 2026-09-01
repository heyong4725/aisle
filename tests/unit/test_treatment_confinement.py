"""Synthetic acceptance tests for the SPEC 420 macOS confinement capability."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_confinement import (
    ConfinementError,
    MacOSPolicy,
    compile_macos_profile,
    run_macos_capability_audit,
    wrap_verified_command,
    write_macos_capability_audit,
)

pytestmark = pytest.mark.unit


def _policy(tmp_path: Path) -> MacOSPolicy:
    visible = tmp_path / "visible"
    output = tmp_path / "output"
    hidden = tmp_path / "hidden"
    for path in (visible, output, hidden):
        path.mkdir()
    runtime_roots = tuple(
        dict.fromkeys(path.resolve() for path in (Path("/bin"), Path("/usr/bin"), Path("/usr/lib")))
    )
    executables = tuple(
        dict.fromkeys(path.resolve() for path in (Path("/bin/bash"), Path("/bin/cat")))
    )
    return MacOSPolicy(
        visible_roots=(visible,),
        output_roots=(output,),
        runtime_read_roots=runtime_roots,
        allowed_executables=executables,
        hidden_roots=(hidden,),
        network_policy="deny-external",
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_profile_is_default_deny_and_names_only_declared_agent_roots(tmp_path: Path):
    """TRT-3/TRT-5: an external profile enforces the explicit visible/output view."""
    policy = _policy(tmp_path)

    compiled = compile_macos_profile(policy)

    assert compiled.text.startswith("(version 1)\n(deny default)\n")
    assert '(import "system.sb")' in compiled.text
    for root in (*policy.visible_roots, *policy.runtime_read_roots):
        assert f"(subpath {json.dumps(str(root))})" in compiled.text
    for root in policy.output_roots:
        assert f"(subpath {json.dumps(str(root))})" in compiled.text
    for executable in policy.allowed_executables:
        assert f"(literal {json.dumps(str(executable))})" in compiled.text
    assert all(str(hidden) not in compiled.text for hidden in policy.hidden_roots)
    assert compiled.sha256 == _sha(compiled.text)
    assert len(compiled.policy_id) == 64


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy, root: MacOSPolicy(
            **{**policy.as_dict(), "visible_roots": (Path("relative"),)}
        ),
        lambda policy, root: MacOSPolicy(
            **{**policy.as_dict(), "runtime_read_roots": (Path("/"),)}
        ),
        lambda policy, root: MacOSPolicy(
            **{**policy.as_dict(), "visible_roots": (root / "hidden" / "nested",)}
        ),
        lambda policy, root: MacOSPolicy(
            **{**policy.as_dict(), "allowed_executables": (Path("/bin/missing-aisle-tool"),)}
        ),
        lambda policy, root: MacOSPolicy(**{**policy.as_dict(), "network_policy": "unrestricted"}),
    ],
)
def test_unsafe_or_unresolved_profiles_fail_closed(tmp_path: Path, mutation):
    """TRT-5: an absent, broad, overlapping, or unresolved adapter policy refuses."""
    policy = mutation(_policy(tmp_path), tmp_path)

    with pytest.raises(ConfinementError):
        compile_macos_profile(policy)


def _fake_adapter(tmp_path: Path) -> Path:
    adapter = tmp_path / "sandbox-exec"
    adapter.write_bytes(b"synthetic adapter")
    adapter.chmod(0o755)
    return adapter


def _attestation(compiled, profile_path: Path, adapter_path: Path) -> dict:
    system_profile = profile_path.parent / "system.sb"
    system_profile.write_bytes(b"synthetic imported system profile")
    return {
        "schema_version": "aisle.macos-confinement-capability.v1",
        "evidence_class": "synthetic_unscored_capability",
        "capability_pass": True,
        "confirmatory_ready": False,
        "adapter": {
            "path": str(adapter_path),
            "sha256": hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
            "compiled_profile_sha256": compiled.sha256,
            "policy_id": compiled.policy_id,
            "imported_system_profile": str(system_profile),
            "imported_system_profile_sha256": hashlib.sha256(
                system_profile.read_bytes()
            ).hexdigest(),
        },
        "profile_path": str(profile_path),
        "cases": [
            {"id": case_id, "passed": True}
            for case_id in (
                "unrestricted_hidden_baseline",
                "visible_read",
                "subprocess_visible_read",
                "absolute_hidden_read",
                "parent_traversal_hidden_read",
                "symlink_hidden_read",
                "subprocess_hidden_read",
                "declared_output_write",
                "hidden_write",
            )
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda report: report.update(capability_pass=False), "did not pass"),
        (
            lambda report: report["adapter"].update(compiled_profile_sha256="b" * 64),
            "profile hash",
        ),
        (lambda report: report["adapter"].update(policy_id="c" * 64), "policy id"),
        (
            lambda report: report["adapter"].update(imported_system_profile_sha256="d" * 64),
            "system profile",
        ),
        (lambda report: report["cases"][0].update(passed=False), "failed case"),
    ],
)
def test_launch_wrapper_refuses_missing_or_drifted_attestation(
    tmp_path: Path, mutation, message: str
):
    """TRT-5: launch fails closed unless the external adapter was verified."""
    compiled = compile_macos_profile(_policy(tmp_path))
    profile_path = tmp_path / "profile.sb"
    profile_path.write_text(compiled.text)
    report = _attestation(compiled, profile_path, _fake_adapter(tmp_path))
    mutation(report)

    with pytest.raises(ConfinementError, match=message):
        wrap_verified_command(["/bin/cat", "allowed.txt"], compiled, profile_path, report)


def test_launch_wrapper_binds_the_exact_profile_and_preserves_argv(tmp_path: Path):
    """TRT-5: a passing external attestation wraps, rather than trusts, the subject."""
    compiled = compile_macos_profile(_policy(tmp_path))
    profile_path = tmp_path / "profile.sb"
    profile_path.write_text(compiled.text)
    adapter_path = _fake_adapter(tmp_path)
    command = ["/bin/cat", "file with spaces.txt"]

    wrapped = wrap_verified_command(
        command,
        compiled,
        profile_path,
        _attestation(compiled, profile_path, adapter_path),
    )

    assert wrapped == [str(adapter_path), "-f", str(profile_path), *command]

    with pytest.raises(ConfinementError, match="confirmatory"):
        wrap_verified_command(
            command,
            compiled,
            profile_path,
            _attestation(compiled, profile_path, adapter_path),
            purpose="confirmatory",
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_live_capability_denies_hidden_path_variants_and_retains_no_hidden_bytes():
    """TRT-6: synthetic absolute/traversal/symlink/subprocess reads are denied."""
    report = run_macos_capability_audit()
    cases = {row["id"]: row for row in report["cases"]}

    assert cases["unrestricted_hidden_baseline"]["passed"]
    assert cases["visible_read"]["passed"]
    assert cases["subprocess_visible_read"]["passed"]
    for case_id in (
        "absolute_hidden_read",
        "parent_traversal_hidden_read",
        "symlink_hidden_read",
        "subprocess_hidden_read",
        "hidden_write",
    ):
        assert cases[case_id]["passed"], cases[case_id]
        assert cases[case_id]["denied"]
        assert not cases[case_id]["sentinel_exposed"]
    assert cases["declared_output_write"]["passed"]
    assert report["summary"] == {
        "baseline_tests": 1,
        "capability_pass": True,
        "declared_allow_tests": 3,
        "denial_detection_rate": 1.0,
        "denial_tests": 5,
        "false_alarm_rate": 0.0,
    }
    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False
    assert report["session_id"].startswith("macos-capability-")
    assert report["recorded_at"].endswith("+00:00")
    assert report["policy"]["network_policy"] == "deny-external"
    assert report["adapter"]["imported_system_profile_sha256"]
    assert "HIDDEN-SYNTHETIC" not in json.dumps(report)


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_capability_writer_is_machine_readable_and_refuses_overwrite(tmp_path: Path):
    """TRT-3/TRT-6: the controller retains one machine-readable unscored audit."""
    output = tmp_path / "audit.json"

    report = write_macos_capability_audit(output)

    assert json.loads(output.read_text()) == report
    with pytest.raises(ConfinementError, match="already exists"):
        write_macos_capability_audit(output)


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_capability_cli_labels_the_result_unscored(tmp_path: Path):
    """TRT-6: the reproducible audit CLI cannot be mistaken for confirmatory evidence."""
    output = tmp_path / "audit.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_confinement",
            "audit-macos",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["evidence_class"] == "synthetic_unscored_capability"
    assert summary["confirmatory_ready"] is False
    assert json.loads(output.read_text())["capability_pass"] is True


def test_non_macos_runtime_refuses_instead_of_simulating_success(monkeypatch):
    """TRT-5/TRT-6: unsupported platforms fail closed, never relabel a mock as evidence."""
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(ConfinementError, match="requires macOS"):
        run_macos_capability_audit()
