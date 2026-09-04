"""Acceptance tests for the SPEC 420 treatment-integrity postflight."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_integrity import create_treatment_manifest
from aisle.harness.treatment_postflight import (
    PostflightError,
    create_postflight_record,
    verify_postflight_record,
    write_postflight_record,
)

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).parents[2]
_FIXTURE = _PROJECT_ROOT / "analysis" / "treatment-integrity" / "manifest-core"


def _inputs(tmp_path: Path) -> tuple[dict, dict, Path, Path]:
    visible = tmp_path / "visible"
    shutil.copytree(_FIXTURE / "visible", visible)
    candidate = json.loads((_FIXTURE / "candidate.json").read_text())
    preflight = create_treatment_manifest(candidate, visible)
    access_log = tmp_path / "hidden-access-log.json"
    access_log.write_text(
        json.dumps(
            {
                "adapter_active": True,
                "complete": True,
                "events": [
                    {
                        "decision": "allow",
                        "surface": "visible_path",
                        "target_class": "visible",
                    },
                    {
                        "decision": "deny",
                        "surface": "git_object",
                        "target_class": "hidden",
                    },
                ],
                "schema_version": "aisle.hidden-access-log.v1",
            }
        )
    )
    return preflight, candidate, visible, access_log


def test_matching_postflight_is_content_addressed_and_synthetic_only(tmp_path: Path):
    """TRT-9: stable synthetic identity passes without authorizing estimation."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)

    first = create_postflight_record(preflight, candidate, visible, access_log)
    second = create_postflight_record(preflight, copy.deepcopy(candidate), visible, access_log)

    assert first == second
    assert first["schema_version"] == "aisle.treatment-postflight.v2"
    assert first["classification"] == "synthetic_pass"
    assert first["evidence_class"] == "synthetic_unscored_postflight"
    assert first["confirmatory_ready"] is False
    assert first["eligible_for_estimate"] is False
    assert first["drift_paths"] == []
    assert first["preflight_immutable_id"] == preflight["immutable_id"]
    assert first["checks"] == {
        "confinement_active": "pass",
        "hidden_access_log": "pass",
        "treatment_identity": "pass",
        "visible_tree": "pass",
    }
    assert first["hidden_access_log"]["events_total"] == 2
    assert first["hidden_access_log"]["hidden_denials"] == 1
    assert first["hidden_access_log"]["hidden_exposures"] == 0
    assert first["immutable_id"].startswith("sha256:")


@pytest.mark.parametrize(
    ("mutation", "drift_path"),
    [
        (
            lambda candidate, visible: (visible / "AGENTS.md").write_text("drifted\n"),
            "repository.visible_files",
        ),
        (
            lambda candidate, visible: candidate["runtime_binaries"][0].update(sha256="9" * 64),
            "runtime_binaries",
        ),
        (
            lambda candidate, visible: candidate["policy"].update(tool_policy_sha256="9" * 64),
            "policy.tool_policy_sha256",
        ),
        (
            lambda candidate, visible: candidate["environment"].update(fingerprint_sha256="9" * 64),
            "environment.fingerprint_sha256",
        ),
        (
            lambda candidate, visible: candidate["model"].update(
                served_identity="synthetic-model-drift"
            ),
            "model.served_identity",
        ),
        (
            lambda candidate, visible: candidate["agent"].update(cli_binary_sha256="9" * 64),
            "agent.cli_binary_sha256",
        ),
        (
            lambda candidate, visible: candidate["confinement"].update(profile_sha256="9" * 64),
            "confinement.profile_sha256",
        ),
    ],
)
def test_treatment_or_visible_tree_drift_is_an_infrastructure_exclusion(
    tmp_path: Path, mutation, drift_path: str
):
    """TRT-9: critical identity or visible-tree drift is never an agent outcome."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    mutation(candidate, visible)

    record = create_postflight_record(preflight, candidate, visible, access_log)

    assert record["classification"] == "infrastructure_exclusion"
    assert record["eligible_for_estimate"] is False
    assert drift_path in record["drift_paths"]
    assert "treatment_drift" in record["exclusion_reasons"]


@pytest.mark.parametrize("served_identity", ["", "unknown", "latest"])
def test_unresolved_postflight_model_identity_is_an_infrastructure_exclusion(
    tmp_path: Path, served_identity: str
):
    """TRT-9: unresolved served-model identity cannot become an agent failure."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    candidate["model"]["served_identity"] = served_identity

    record = create_postflight_record(preflight, candidate, visible, access_log)

    assert record["classification"] == "infrastructure_exclusion"
    assert record["checks"]["treatment_identity"] == "unresolved"
    assert record["exclusion_reasons"] == ["current_treatment_unresolved"]
    assert "model.served_identity" in record["diagnostic"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda log: log.update(complete=False), "hidden_access_log_incomplete"),
        (lambda log: log.update(adapter_active=False), "confinement_inactive"),
        (
            lambda log: log["events"].append(
                {
                    "decision": "allow",
                    "surface": "absolute_path",
                    "target_class": "hidden",
                }
            ),
            "hidden_material_exposure",
        ),
        (
            lambda log: log["events"][0].update(payload="synthetic-hidden-bytes"),
            "hidden_access_log_invalid",
        ),
    ],
)
def test_incomplete_inactive_exposing_or_payload_bearing_audit_excludes(
    tmp_path: Path, mutation, reason: str
):
    """TRT-9: unusable confinement evidence excludes without retaining bytes."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    log = json.loads(access_log.read_text())
    mutation(log)
    access_log.write_text(json.dumps(log))

    record = create_postflight_record(preflight, candidate, visible, access_log)

    assert record["classification"] == "infrastructure_exclusion"
    assert reason in record["exclusion_reasons"]
    assert "synthetic-hidden-bytes" not in json.dumps(record)


def test_unreadable_hidden_access_log_is_an_infrastructure_exclusion(tmp_path: Path):
    """TRT-9: an unreadable audit source produces a retained exclusion record."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    access_log.unlink()

    record = create_postflight_record(preflight, candidate, visible, access_log)

    assert record["classification"] == "infrastructure_exclusion"
    assert record["checks"]["hidden_access_log"] == "unreadable"
    assert record["exclusion_reasons"] == ["hidden_access_log_unreadable"]
    assert "hidden_access_log" not in record or "sha256" not in record["hidden_access_log"]


def test_noncanonical_preflight_is_retained_as_an_infrastructure_exclusion(tmp_path: Path):
    """TRT-9: an unusable preflight source excludes instead of raising past audit."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    preflight["sampling"]["temperature"] = float("nan")

    record = create_postflight_record(preflight, candidate, visible, access_log)

    assert record["classification"] == "infrastructure_exclusion"
    assert "preflight_unusable" in record["exclusion_reasons"]
    assert record["checks"]["treatment_identity"] == "unresolved"


def test_postflight_writer_retains_machine_readable_record_and_refuses_overwrite(
    tmp_path: Path,
):
    """TRT-9: one immutable postflight is retained without silent replacement."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    output = tmp_path / "evidence" / "postflight.json"

    written = write_postflight_record(preflight, candidate, visible, access_log, output)

    assert json.loads(output.read_text()) == written
    with pytest.raises(PostflightError, match="already exists"):
        write_postflight_record(preflight, candidate, visible, access_log, output)


def test_postflight_verifier_detects_retained_record_drift(tmp_path: Path):
    """TRT-9: postflight evidence is independently content-address verified."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    record = create_postflight_record(preflight, candidate, visible, access_log)

    assert verify_postflight_record(record) == record
    record["checks"]["visible_tree"] = "drift"
    with pytest.raises(PostflightError, match="immutable_id"):
        verify_postflight_record(record)

    with pytest.raises(PostflightError, match="canonical"):
        verify_postflight_record({"immutable_id": "sha256:bad", "value": float("nan")})


def test_postflight_cli_creates_then_verifies_machine_readable_evidence(tmp_path: Path):
    """TRT-9: the controller exposes reproducible create and verify commands."""
    preflight, candidate, visible, access_log = _inputs(tmp_path)
    preflight_path = tmp_path / "preflight.json"
    candidate_path = tmp_path / "candidate.json"
    output = tmp_path / "postflight.json"
    preflight_path.write_text(json.dumps(preflight))
    candidate_path.write_text(json.dumps(candidate))

    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_postflight",
            "create",
            "--preflight",
            str(preflight_path),
            "--candidate",
            str(candidate_path),
            "--root",
            str(visible),
            "--hidden-access-log",
            str(access_log),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    summary = json.loads(created.stdout)
    assert summary["ok"] is True
    assert summary["classification"] == "synthetic_pass"
    assert summary["immutable_id"] == json.loads(output.read_text())["immutable_id"]

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_postflight",
            "verify",
            "--postflight",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout) == {
        "classification": "synthetic_pass",
        "immutable_id": summary["immutable_id"],
        "ok": True,
    }
