"""Acceptance tests for the SPEC 420 ambient-state isolation boundary."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_ambient import (
    AmbientIsolationError,
    amend_declared_environment,
    build_declared_environment,
    run_ambient_capability_audit,
    spawn_isolated_process,
    write_ambient_capability_audit,
)

pytestmark = pytest.mark.unit
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source_environment(operator_home: Path) -> dict[str, str]:
    return {
        "HOME": str(operator_home),
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "CLAUDE_CONFIG_DIR": str(operator_home / ".claude"),
        "CODEX_HOME": str(operator_home / ".codex"),
        "XDG_CONFIG_HOME": str(operator_home / ".config"),
        "XDG_DATA_HOME": str(operator_home / ".local" / "share"),
        "XDG_CACHE_HOME": str(operator_home / ".cache"),
        "XDG_STATE_HOME": str(operator_home / ".local" / "state"),
        "TMPDIR": str(operator_home / "tmp"),
        "ANTHROPIC_API_KEY": "synthetic-secret",
        "OPENAI_API_KEY": "synthetic-secret",
        "SSH_AUTH_SOCK": str(operator_home / "agent.sock"),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={operator_home / 'bus.sock'}",
        "AISLE_UNRELATED_SENTINEL": "synthetic-ambient-state",
    }


def test_declared_environment_replaces_ambient_home_config_cache_and_endpoints(tmp_path: Path):
    """TRT-7: only declared state enters HOME/config/XDG/tmp and the child env."""
    operator_home = tmp_path / "operator"
    operator_home.mkdir()
    source = _source_environment(operator_home)
    home = (tmp_path / "session" / "agent_home").resolve()

    env, record = build_declared_environment(
        home,
        source_env=source,
        env_baseline_oid="a" * 40,
    )

    expected_paths = {
        "HOME": home,
        "CLAUDE_CONFIG_DIR": home / ".claude",
        "CODEX_HOME": home / ".codex",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_STATE_HOME": home / ".local" / "state",
        "TMPDIR": home / ".tmp",
        "TMP": home / ".tmp",
        "TEMP": home / ".tmp",
    }
    for name, path in expected_paths.items():
        assert env[name] == str(path)
        assert path.is_dir()
        assert path.resolve().is_relative_to(home)
    assert env["PATH"] == os.defpath
    assert env["LANG"] == "C.UTF-8"
    assert env["AISLE_ENV_BASELINE"] == "a" * 40
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "SSH_AUTH_SOCK",
        "DBUS_SESSION_BUS_ADDRESS",
        "AISLE_UNRELATED_SENTINEL",
    ):
        assert name not in env
    assert record["schema_version"] == "aisle.ambient-baseline.v1"
    assert record["environment_keys"] == sorted(env)
    assert (
        record["environment_sha256"]
        == hashlib.sha256(
            json.dumps(env, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
    )
    assert record["inherited_fd_policy"] == "close-all-nonstandard"
    assert record["local_endpoint_policy"] == "no-ambient-endpoint-variables"


@pytest.mark.parametrize(
    ("home", "pin", "message"),
    [
        (Path("relative"), "a" * 40, "absolute"),
        (Path("/"), "a" * 40, "filesystem root"),
        (Path("/tmp/aisle-ambient-test"), "short", "baseline"),
    ],
)
def test_unsafe_or_ambiguous_environment_inputs_fail_closed(home: Path, pin: str, message: str):
    """TRT-7: a broad home or unresolved baseline cannot create a launch env."""
    with pytest.raises(AmbientIsolationError, match=message):
        build_declared_environment(home, source_env={"PATH": os.defpath}, env_baseline_oid=pin)


def test_spawn_rejects_environment_mutation_and_forbidden_fd_overrides(tmp_path: Path):
    """TRT-7: launcher enforcement rejects added env channels and fd bypasses."""
    env, record = build_declared_environment(
        (tmp_path / "agent_home").resolve(), source_env={"PATH": os.defpath}
    )
    mutated = {**env, "SSH_AUTH_SOCK": "/tmp/operator.sock"}

    with pytest.raises(AmbientIsolationError, match="environment drift"):
        spawn_isolated_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            environment=mutated,
            environment_record=record,
        )


def test_controller_amendment_accepts_only_named_post_baseline_additions(tmp_path: Path):
    """TRT-7: controller compatibility state is explicit, bounded, and re-attested."""
    env, record = build_declared_environment(
        (tmp_path / "agent_home").resolve(), source_env={"PATH": os.defpath}
    )
    original_id = record["immutable_id"]
    env["PYTHONPATH"] = str(tmp_path / "controller-compat")

    amend_declared_environment(
        env,
        record,
        added_keys=("PYTHONPATH",),
        reason="historical-baseline-compat",
    )

    assert record["environment_keys"] == sorted(env)
    assert record["immutable_id"] != original_id
    assert record["controller_amendments"] == [
        {"added_keys": ["PYTHONPATH"], "reason": "historical-baseline-compat"}
    ]

    env["SSH_AUTH_SOCK"] = "/tmp/operator.sock"
    with pytest.raises(AmbientIsolationError, match="unnamed environment drift"):
        amend_declared_environment(
            env,
            record,
            added_keys=("ANOTHER_KEY",),
            reason="test",
        )
    with pytest.raises(AmbientIsolationError, match="pass_fds"):
        spawn_isolated_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            environment=env,
            environment_record=record,
            pass_fds=(3,),
        )


def test_spawn_closes_inheritable_descriptors_and_delivers_only_declared_env(tmp_path: Path):
    """TRT-7: a real child cannot observe an inheritable controller fd or ambient env."""
    env, record = build_declared_environment(
        (tmp_path / "agent_home").resolve(),
        source_env={"PATH": os.defpath, "AISLE_UNRELATED_SENTINEL": "hidden"},
    )
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, True)
        script = (
            "import json, os, sys; "
            "fd=int(sys.argv[1]); "
            "\ntry:\n os.fstat(fd); opened=True"
            "\nexcept OSError:\n opened=False"
            "\nprint(json.dumps({'fd_open': opened, 'env': dict(os.environ)}, sort_keys=True))"
        )
        process = spawn_isolated_process(
            [sys.executable, "-c", script, str(read_fd)],
            cwd=tmp_path,
            environment=env,
            environment_record=record,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert process.returncode == 0, stderr
    observed = json.loads(stdout)
    assert observed["fd_open"] is False
    assert observed["env"] == env
    assert "AISLE_UNRELATED_SENTINEL" not in observed["env"]


def test_synthetic_audit_covers_each_ambient_channel_without_retaining_sentinel():
    """TRT-7: independent synthetic probes quantify each declared isolation channel."""
    report = run_ambient_capability_audit()
    cases = {row["id"]: row for row in report["cases"]}

    assert set(cases) == {
        "agent_config_homes_rebound",
        "credential_environment_removed",
        "declared_path_forwarded",
        "home_rebound",
        "inherited_fd_closed",
        "socket_endpoint_environment_removed",
        "unrelated_environment_removed",
        "xdg_and_tmp_rebound",
    }
    assert all(row["passed"] for row in cases.values())
    assert report["summary"] == {
        "checks": 8,
        "detection_rate": 1.0,
        "false_alarm_checks": 1,
        "false_alarm_rate": 0.0,
    }
    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False
    assert report["evidence_class"] == "synthetic_unscored_ambient_capability"
    assert len(report["implementation_sha256"]) == 64
    assert report["session_id"].startswith("ambient-capability-")
    assert report["platform"]["system"]
    assert report["platform"]["machine"]
    assert report["platform"]["python"]
    assert report["randomization_seed"] is None
    assert report["randomization_status"] == "not-applicable-synthetic-capability"
    rendered = json.dumps(report, sort_keys=True)
    assert "SYNTHETIC-AMBIENT-SENTINEL" not in rendered
    assert "SYNTHETIC-CREDENTIAL" not in rendered


def test_audit_writer_and_cli_are_machine_readable_non_overwriting(tmp_path: Path):
    """TRT-7: reproducible evidence is retained once and labeled unscored."""
    output = tmp_path / "audit.json"
    report = write_ambient_capability_audit(output)

    assert json.loads(output.read_text()) == report
    with pytest.raises(AmbientIsolationError, match="already exists"):
        write_ambient_capability_audit(output)

    cli_output = tmp_path / "cli-audit.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_ambient",
            "audit-synthetic",
            "--output",
            str(cli_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["confirmatory_ready"] is False
    assert json.loads(cli_output.read_text())["capability_pass"] is True


def test_primary_audit_is_bound_to_the_retained_implementation_and_unscored():
    """TRT-7: retained raw evidence names its implementation and honest scope."""
    artifact = (
        _PROJECT_ROOT
        / "analysis"
        / "treatment-integrity"
        / "ambient-capability"
        / "audit-provenance-complete.json"
    )
    report = json.loads(artifact.read_text())
    source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_ambient.py"

    assert report["implementation_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False
    assert report["summary"]["detection_rate"] == 1.0
    assert report["summary"]["false_alarm_rate"] == 0.0
    rendered = artifact.read_text()
    assert "SYNTHETIC-AMBIENT-SENTINEL" not in rendered
    assert "SYNTHETIC-CREDENTIAL" not in rendered
