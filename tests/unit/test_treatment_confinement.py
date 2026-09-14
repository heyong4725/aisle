"""Synthetic acceptance tests for the SPEC 420 macOS confinement capability."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
        "schema_version": "aisle.macos-confinement-capability.v4",
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
        "policy": {"network_policy": compiled.network_policy},
        "cases": [
            {"id": case_id, "passed": True}
            for case_id in (
                "unrestricted_tcp_baseline",
                "tcp_read",
                "unrestricted_unix_socket_baseline",
                "unix_socket_network_control",
                "unix_socket_read",
                "unrestricted_exec_baseline",
                "unlisted_executable",
                "unrestricted_hidden_baseline",
                "unrestricted_alternate_worktree_baseline",
                "unrestricted_git_object_baseline",
                "visible_read",
                "subprocess_visible_read",
                "visible_git_object_read",
                "absolute_hidden_read",
                "parent_traversal_hidden_read",
                "symlink_hidden_read",
                "subprocess_hidden_read",
                "alternate_worktree_hidden_read",
                "git_object_hidden_read",
                "declared_output_write",
                "hidden_write",
            )
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda report: report.update(schema_version="aisle.macos-confinement-capability.v3"),
            "schema is unsupported",
        ),
        *[
            (
                lambda report, missing=missing: report.update(
                    cases=[row for row in report["cases"] if row["id"] != missing]
                ),
                "failed case",
            )
            for missing in (
                "unrestricted_unix_socket_baseline",
                "unix_socket_network_control",
                "unix_socket_read",
            )
        ],
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
    """TRT-6: synthetic path, worktree, object, and subprocess reads are denied."""
    report = run_macos_capability_audit()
    cases = {row["id"]: row for row in report["cases"]}

    assert cases["unrestricted_hidden_baseline"]["passed"]
    assert cases["unrestricted_alternate_worktree_baseline"]["passed"]
    assert cases["unrestricted_git_object_baseline"]["passed"]
    assert cases["visible_read"]["passed"]
    assert cases["subprocess_visible_read"]["passed"]
    assert cases["visible_git_object_read"]["passed"]
    for case_id in (
        "absolute_hidden_read",
        "alternate_worktree_hidden_read",
        "git_object_hidden_read",
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
        "baseline_tests": 7,
        "capability_pass": True,
        "declared_allow_tests": 4,
        "denial_detection_rate": 1.0,
        "denial_tests": 10,
        "false_alarm_rate": 0.0,
    }
    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False
    assert report["session_id"].startswith("macos-capability-")
    assert report["recorded_at"].endswith("+00:00")
    assert report["policy"]["network_policy"] == "deny-external"
    assert report["policy_source"] == "synthetic"
    from aisle.harness.treatment_confinement import required_case_ids

    assert set(cases) == required_case_ids("deny-external")
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


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_live_capability_blocks_tcp_reads_and_unlisted_executables():
    """TRT-6/THR-10: real socket and exec denials need working unrestricted controls."""
    report = run_macos_capability_audit()
    cases = {row["id"]: row for row in report["cases"]}
    for baseline in ("unrestricted_tcp_baseline", "unrestricted_exec_baseline"):
        assert cases[baseline]["passed"] and cases[baseline]["sentinel_exposed"]
    for denial in ("tcp_read", "unlisted_executable"):
        assert cases[denial]["passed"] and cases[denial]["denied"]
        assert not cases[denial]["sentinel_exposed"]
    assert report["confirmatory_ready"] is False


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_live_capability_blocks_unix_socket_with_matching_network_control():
    """TRT-5/TRT-6/TRT-7: local socket denial needs executable and network controls."""
    report = run_macos_capability_audit()
    cases = {row["id"]: row for row in report["cases"]}
    for baseline in ("unrestricted_unix_socket_baseline", "unix_socket_network_control"):
        assert cases[baseline]["passed"] and cases[baseline]["sentinel_exposed"]
    denial = cases["unix_socket_read"]
    assert denial["passed"] and denial["denied"]
    assert not denial["sentinel_exposed"]
    assert report["confirmatory_ready"] is False


# --- session-bound attestation and unsupported relay authority ---


def _session_policy(tmp_path: Path) -> MacOSPolicy:
    """A policy shaped like a real arm's: the audit's probes must be admitted
    executables and the Apple developer tree a runtime read root."""
    from aisle.harness.treatment_confinement import _apple_git_runtime

    visible = tmp_path / "view"
    output = tmp_path / "scratch"
    hidden = tmp_path / "controller"
    for path in (visible, output, hidden):
        path.mkdir()
    git, developer_root = _apple_git_runtime(cwd=tmp_path)
    runtime_roots = tuple(
        dict.fromkeys(
            Path(p).resolve()
            for p in ("/Library/Apple", "/System", "/bin", "/usr/bin", "/usr/lib", "/usr/share")
        )
    ) + (developer_root,)
    executables = tuple(
        dict.fromkeys(Path(p).resolve() for p in ("/bin/bash", "/bin/cat", "/usr/bin/nc"))
    ) + (git,)
    return MacOSPolicy(
        visible_roots=(visible.resolve(),),
        output_roots=(output.resolve(),),
        runtime_read_roots=runtime_roots,
        allowed_executables=executables,
        hidden_roots=(hidden.resolve(),),
        network_policy="deny-external",
    )


def test_old_loopback_policy_parses_but_is_not_compilable(tmp_path: Path):
    """TRT-5: old policy declarations remain readable but cannot grant broad endpoints."""
    from aisle.harness.treatment_confinement import NETWORK_POLICIES

    assert NETWORK_POLICIES == ("deny-external",)
    base = _policy(tmp_path)
    assert "loopback_port" not in base.canonical_dict()
    assert MacOSPolicy.from_canonical(base.canonical_dict()) == base
    pinned = MacOSPolicy(**{**base.as_dict(), "network_policy": "loopback", "loopback_port": 4321})
    assert MacOSPolicy.from_canonical(pinned.canonical_dict()) == pinned
    with pytest.raises(ConfinementError, match="cannot isolate the provider relay"):
        compile_macos_profile(pinned)
    with pytest.raises(ConfinementError, match="cannot carry a relay port"):
        compile_macos_profile(MacOSPolicy(**{**base.as_dict(), "loopback_port": 4321}))


def test_provider_admission_refuses_loopback_even_when_ports_match():
    """TRT-5/MON-8: matching port numbers cannot establish exclusive relay authority."""
    from aisle.harness.treatment_confinement import verify_relay_port

    verify_relay_port({"network_policy": "deny-external"}, {"base_url": "https://x"})
    verify_relay_port(None, {"base_url": "https://x"})
    for port in (None, 4321, 4322):
        with pytest.raises(ValueError, match="cannot isolate the provider relay"):
            verify_relay_port(
                {"network_policy": "loopback", "loopback_port": 4321},
                {"base_url": "https://x", "relay_port": port},
            )


def test_roots_must_be_printable_ascii(tmp_path: Path):
    """TRT-5: a root the SBPL literal could not name exactly is refused."""
    odd = tmp_path / "visé"
    odd.mkdir()
    with pytest.raises(ConfinementError, match="non-ASCII"):
        compile_macos_profile(
            MacOSPolicy(**{**_policy(tmp_path).as_dict(), "visible_roots": (odd,)})
        )


def test_required_case_ids_refuse_unsupported_network_authority():
    """TRT-6: unsupported loopback authority has no passing audit case matrix."""
    from aisle.harness.treatment_confinement import required_case_ids

    assert "tcp_read" in required_case_ids("deny-external")
    for network in ("loopback", "unrestricted"):
        with pytest.raises(ConfinementError):
            required_case_ids(network)


def test_launch_wrapper_requires_matching_attested_network_policy(tmp_path: Path):
    """TRT-5: an attestation must name the same supported policy as the profile."""
    compiled = compile_macos_profile(_policy(tmp_path))
    profile = tmp_path / "profile.sb"
    profile.write_text(compiled.text)
    report = _attestation(compiled, profile, _fake_adapter(tmp_path))
    for broken in ({"network_policy": "loopback"}, [], None):
        report["policy"] = broken
        with pytest.raises(ConfinementError, match="network policy"):
            wrap_verified_command(["/bin/cat", "x"], compiled, profile, report)


def test_sentinel_placement_is_transactional_and_refuses_occupied_paths(tmp_path: Path):
    """TRT-6: a sentinel that cannot be created removes the ones already placed,
    an occupied sentinel path refuses, and an unremovable sentinel is an error."""
    import contextlib

    from aisle.harness import treatment_confinement as tc

    policy = _policy(tmp_path)
    visible, output, hidden = (
        policy.visible_roots[0],
        policy.output_roots[0],
        policy.hidden_roots[0],
    )
    hidden.chmod(0o500)
    try:
        with pytest.raises(ConfinementError, match="could not be created"):
            with contextlib.ExitStack() as cleanup:
                tc._place_sentinels(policy, cleanup)
    finally:
        hidden.chmod(0o700)
    assert not (visible / ".aisle-capability").exists()
    assert not (output / ".aisle-capability").exists()
    (output / ".aisle-capability").write_text("occupied")
    with pytest.raises(ConfinementError, match="already exists"):
        with contextlib.ExitStack() as cleanup:
            tc._place_sentinels(policy, cleanup)
    assert not (visible / ".aisle-capability").exists()
    (output / ".aisle-capability").unlink()
    with contextlib.ExitStack() as cleanup:
        placed = tc._place_sentinels(policy, cleanup)
        assert placed == tuple(root / ".aisle-capability" for root in (visible, output, hidden))
        assert all(path.is_dir() for path in placed)
    assert not any(path.exists() for path in placed)
    # residue of a dead audit process is reclaimed; a live owner's is not
    dead = visible / ".aisle-capability"
    dead.mkdir()
    (dead / "leftover.txt").write_text("x")
    (dead / ".owner.json").write_text(
        json.dumps({"schema_version": "aisle.sentinel-owner.v1", "pid": 2**22 + 12345})
    )
    with contextlib.ExitStack() as cleanup:
        placed = tc._place_sentinels(policy, cleanup)
        assert not (dead / "leftover.txt").exists()
        assert json.loads((dead / ".owner.json").read_text())["pid"] == os.getpid()
    assert not dead.exists()
    dead.mkdir()
    (dead / ".owner.json").write_text(
        json.dumps({"schema_version": "aisle.sentinel-owner.v1", "pid": os.getpid()})
    )
    with pytest.raises(ConfinementError, match="already exists"):
        with contextlib.ExitStack() as cleanup:
            tc._place_sentinels(policy, cleanup)
    shutil.rmtree(dead)
    stuck = tmp_path / ".aisle-capability"
    stuck.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tc.shutil, "rmtree", lambda *args, **kwargs: None)
        with pytest.raises(ConfinementError, match="could not be removed"):
            tc._remove_sentinel(stuck)


def test_last_directory_prefers_the_last_directory_and_refuses_file_only_roots(tmp_path: Path):
    """TRT-6: sentinels go into the last directory root; file-only roots refuse."""
    from aisle.harness.treatment_confinement import _last_directory

    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    editable = tmp_path / "graph.yaml"
    editable.write_text("nodes: []\n")
    assert _last_directory("output_roots", (first, second, editable)) == second
    with pytest.raises(ConfinementError, match="holds no directory"):
        _last_directory("output_roots", (editable,))


def _policy_json(tmp_path: Path) -> dict:
    return _policy(tmp_path).canonical_dict()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda body: "{not json", "unreadable"),
        (lambda body: "[]", "exactly the MacOSPolicy fields"),
        (lambda body: json.dumps({"visible_roots": []}), "exactly the MacOSPolicy fields"),
        (
            lambda body: json.dumps({**body, "extra": 1}),
            "exactly the MacOSPolicy fields",
        ),
        (lambda body: json.dumps({**body, "network_policy": ["loopback"]}), "lists of paths"),
        (lambda body: json.dumps({**body, "hidden_roots": "/x"}), "lists of paths"),
        (lambda body: json.dumps({**body, "visible_roots": [1]}), "lists of paths"),
    ],
)
def test_load_policy_refuses_malformed_files(tmp_path: Path, mutate, message: str):
    """TRT-5/CON-8: a malformed `--policy` file refuses instead of attesting."""
    from aisle.harness.treatment_confinement import load_policy

    policy_file = tmp_path / "policy.json"
    policy_file.write_text(mutate(_policy_json(tmp_path)))
    with pytest.raises(ConfinementError, match=message):
        load_policy(policy_file)
    with pytest.raises(ConfinementError, match="unreadable"):
        load_policy(tmp_path / "absent.json")


def test_load_policy_round_trips_the_canonical_form_and_the_cli_refuses_with_json(
    tmp_path: Path, capsys
):
    """TRT-5/CON-8: canonical JSON loads back to the same policy; a bad policy file
    makes `audit-macos` exit 2 with JSON on stderr and nothing on stdout."""
    from aisle.harness.treatment_confinement import load_policy, main

    policy = _policy(tmp_path)
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy.canonical_dict()))
    assert load_policy(policy_file) == policy
    policy_file.write_text("{")
    assert main(["audit-macos", "--policy", str(policy_file), "--output", str(tmp_path / "o")]) == 2
    out, err = capsys.readouterr()
    assert out == "" and json.loads(err)["ok"] is False


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_session_policy_audit_binds_the_launch_profile_and_leaves_no_sentinels(tmp_path: Path):
    """TRT-5/TRT-6/TRT-7: the audit run under a session's exact policy yields an
    attestation the launch wrapper accepts, proves network connects are refused,
    and removes every sentinel it placed in the roots."""
    from aisle.harness.treatment_confinement import SANDBOX_EXEC, required_case_ids

    before = set(Path("/private/tmp").glob("aisle-sock-*"))
    policy = _session_policy(tmp_path)
    compiled = compile_macos_profile(policy)
    report = run_macos_capability_audit(policy=policy)
    cases = {row["id"]: row for row in report["cases"]}
    assert report["capability_pass"] is True, [c for c in report["cases"] if not c["passed"]]
    assert report["policy_source"] == "session"
    expected_policy = policy.canonical_dict()
    expected_policy["hidden_roots"] = [
        "sha256:" + hashlib.sha256(str(root).encode()).hexdigest() for root in policy.hidden_roots
    ]
    expected_policy["hidden_roots_redacted"] = True
    assert report["policy"] == expected_policy
    assert str(policy.hidden_roots[0]) not in json.dumps(report)
    assert report["adapter"]["compiled_profile_sha256"] == compiled.sha256
    assert report["adapter"]["policy_id"] == compiled.policy_id
    assert set(cases) == required_case_ids("deny-external")
    assert report["summary"] == {
        "baseline_tests": 7,
        "capability_pass": True,
        "declared_allow_tests": 4,
        "denial_detection_rate": 1.0,
        "denial_tests": 10,
        "false_alarm_rate": 0.0,
    }
    for denial in ("tcp_read",):
        assert cases[denial]["passed"] and cases[denial]["denied"], cases[denial]
        assert not cases[denial]["sentinel_exposed"]
    assert set(Path("/private/tmp").glob("aisle-sock-*")) == before
    assert cases["parent_traversal_hidden_read"]["denied"]
    assert "HIDDEN-SYNTHETIC" not in json.dumps(report)
    for root in (*policy.visible_roots, *policy.output_roots, *policy.hidden_roots):
        assert not (root / ".aisle-capability").exists()
        assert list(root.iterdir()) == []
    profile_path = tmp_path / "profile.sb"
    profile_path.write_text(compiled.text)
    wrapped = wrap_verified_command(["/bin/cat", "x"], compiled, profile_path, report)
    assert wrapped[:3] == [str(SANDBOX_EXEC), "-f", str(profile_path)]


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda policy, root: MacOSPolicy(
                **{
                    **policy.as_dict(),
                    "allowed_executables": (
                        *policy.allowed_executables,
                        Path("/usr/bin/printf").resolve(),
                    ),
                }
            ),
            "unlisted-executable control",
        ),
        (
            lambda policy, root: MacOSPolicy(
                **{
                    **policy.as_dict(),
                    "allowed_executables": tuple(
                        p for p in policy.allowed_executables if p.name != "nc"
                    ),
                }
            ),
            "Unix-socket probe",
        ),
        (
            lambda policy, root: MacOSPolicy(
                **{
                    **policy.as_dict(),
                    "allowed_executables": tuple(
                        p for p in policy.allowed_executables if p.name != "cat"
                    ),
                }
            ),
            "audit probes",
        ),
        (
            lambda policy, root: (root / "view" / ".aisle-capability").mkdir() or policy,
            "sentinel",
        ),
    ],
)
def test_session_policy_audit_refuses_policies_it_cannot_prove(tmp_path: Path, mutate, message):
    """TRT-5: a session policy that lacks the probes, admits the unlisted-executable
    control, or already carries sentinel paths refuses instead of attesting."""
    policy = mutate(_session_policy(tmp_path), tmp_path)
    with pytest.raises(ConfinementError, match=message):
        run_macos_capability_audit(policy=policy)


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_capability_cli_accepts_a_session_policy_file(tmp_path: Path):
    """TRT-6/CON-8: `audit-macos --policy` retains an attestation bound to that policy."""
    policy = _session_policy(tmp_path)
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy.canonical_dict()))
    output = tmp_path / "audit.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_confinement",
            "audit-macos",
            "--policy",
            str(policy_file),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    report = json.loads(output.read_text())
    assert report["policy_source"] == "session"
    assert report["adapter"]["policy_id"] == compile_macos_profile(policy).policy_id


def test_socket_probe_uses_an_admitted_client_and_never_widens_the_grants(tmp_path: Path):
    """TRT-5: the Unix-socket probe is nc when admitted, else an admitted Python
    interpreter, else the policy refuses; the audit adds no executable of its own."""
    from aisle.harness.treatment_confinement import _socket_client

    base = _policy(tmp_path).as_dict()
    python = Path(sys.executable).resolve()
    # admitted executables are canonical paths (on Linux /usr/bin/nc is an
    # alternatives link), and the audit resolves its probe the same way
    netcat = Path("/usr/bin/nc").resolve()
    with_nc = MacOSPolicy(**{**base, "allowed_executables": (*base["allowed_executables"], netcat)})
    assert _socket_client(with_nc) == [str(netcat), "-w", "2", "-U"]
    with_python = MacOSPolicy(
        **{**base, "allowed_executables": (*base["allowed_executables"], python)}
    )
    argv = _socket_client(with_python)
    assert argv[:3] == [str(python), "-I", "-c"] and "AF_UNIX" in argv[3]
    with pytest.raises(ConfinementError, match="Unix-socket probe"):
        _socket_client(MacOSPolicy(**base))


def test_corrupt_or_live_owner_markers_are_never_reclaimed(tmp_path: Path):
    """TRT-6: only a well-formed marker naming a dead process authorizes reclaiming
    a leftover sentinel; corrupt, missing or live markers refuse."""
    from aisle.harness.treatment_confinement import _reclaimable

    sentinel = tmp_path / ".aisle-capability"
    sentinel.mkdir()
    assert not _reclaimable(sentinel)
    marker = sentinel / ".owner.json"
    marker.write_text("{corrupt")
    assert not _reclaimable(sentinel)
    marker.write_text(json.dumps({"schema_version": "other", "pid": 2**22 + 1}))
    assert not _reclaimable(sentinel)
    marker.write_text(json.dumps({"schema_version": "aisle.sentinel-owner.v1", "pid": os.getpid()}))
    assert not _reclaimable(sentinel)
    marker.write_text(json.dumps({"schema_version": "aisle.sentinel-owner.v1", "pid": 2**22 + 1}))
    assert _reclaimable(sentinel)


def test_loopback_endpoint_authority_refuses_before_profile_compilation(tmp_path: Path):
    """TRT-5/TRT-7: a port shared by local addresses cannot authorize a relay-only grant."""
    policy = MacOSPolicy(
        **{**_policy(tmp_path).as_dict(), "network_policy": "loopback", "loopback_port": 4321}
    )
    with pytest.raises(ConfinementError, match="cannot isolate the provider relay"):
        compile_macos_profile(policy)


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_same_port_ipv4_and_ipv6_services_cannot_receive_a_relay_grant(tmp_path):
    """TRT-5/TRT-7: distinct live IPv4/IPv6 endpoints can share the proposed relay port."""
    import socket
    import threading

    from aisle.harness import treatment_confinement as tc

    base = _session_policy(tmp_path)
    with socket.socket() as relay, socket.socket(socket.AF_INET6) as foreign:
        relay.bind(("127.0.0.1", 0))
        relay.listen()
        port = relay.getsockname()[1]
        foreign.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        foreign.bind(("::1", port))
        foreign.listen()
        foreign.settimeout(0.1)
        stopped = threading.Event()
        server = threading.Thread(
            target=tc._serve_once, args=(foreign, b"FOREIGN-SYNTHETIC-CANARY\n", stopped)
        )
        server.start()
        try:
            command = ["/bin/bash", "-c", f"exec 3<>/dev/tcp/::1/{port} && /bin/cat <&3"]
            baseline = subprocess.run(command, capture_output=True, timeout=5)
            assert baseline.returncode == 0
            assert baseline.stdout == b"FOREIGN-SYNTHETIC-CANARY\n"
            policy = MacOSPolicy(
                **{**base.as_dict(), "network_policy": "loopback", "loopback_port": port}
            )
            with pytest.raises(ConfinementError, match="cannot isolate the provider relay"):
                compile_macos_profile(policy)
            # The supported policy actually denies this same reachable endpoint.
            profile = tmp_path / "profile.sb"
            profile.write_text(compile_macos_profile(base).text)
            confined = subprocess.run(tc._wrapped(profile, command), capture_output=True, timeout=5)
            assert confined.returncode != 0
            assert b"FOREIGN-SYNTHETIC-CANARY" not in confined.stdout + confined.stderr
        finally:
            stopped.set()
            server.join(timeout=2)


def test_cached_loopback_attestation_cannot_launch_a_command(tmp_path: Path):
    """TRT-5: even a previously passing loopback report cannot authorize a launch."""
    from dataclasses import replace

    compiled = replace(compile_macos_profile(_policy(tmp_path)), network_policy="loopback")
    profile = tmp_path / "profile.sb"
    profile.write_text(compiled.text)
    report = _attestation(compiled, profile, _fake_adapter(tmp_path))
    report["cases"] = [row for row in report["cases"] if row["id"] != "tcp_read"] + [
        {"id": name, "passed": True}
        for name in ("loopback_tcp_read", "foreign_loopback_tcp_read", "external_tcp_read")
    ]
    with pytest.raises(ConfinementError, match="cannot isolate the provider relay"):
        wrap_verified_command(["/bin/cat", "x"], compiled, profile, report)


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec capability is macOS-only")
def test_loopback_audit_refuses_before_subprocesses_or_sentinel_writes(tmp_path, monkeypatch):
    """TRT-5/TRT-6: unsupported endpoint authority refuses before touching session roots."""
    from aisle.harness import treatment_confinement as tc

    policy = MacOSPolicy(
        **{**_policy(tmp_path).as_dict(), "network_policy": "loopback", "loopback_port": 4321}
    )
    monkeypatch.setattr(tc, "_apple_git_runtime", lambda **_: pytest.fail("spawned a probe"))
    with pytest.raises(ConfinementError, match="cannot isolate the provider relay"):
        run_macos_capability_audit(policy)
    assert all(
        not list(path.iterdir())
        for path in (*policy.visible_roots, *policy.output_roots, *policy.hidden_roots)
    )
