"""Fail-closed validation for independent-reproduction release bundles."""

import hashlib
from typing import Any


class ReproductionError(ValueError):
    """Raised when a reproduction bundle is not self-describing."""


def redact_double_blind(manifest: dict[str, Any]) -> dict[str, Any]:
    """Produce deterministic participant view without held-out truth or outcomes."""
    forbidden = {"seed", "truth", "outcome", "oracle_state"}
    view = {key: value for key, value in manifest.items() if key not in forbidden}
    view["redaction_hash"] = hashlib.sha256(str(sorted(view.items())).encode()).hexdigest()
    return view


def validate_layered_comparison(layers: dict[str, dict[str, Any]]) -> None:
    """Require independent CON-5 comparison layers with evidence for each."""
    required = {"artifact", "cadence", "physics", "statistics"}
    if set(layers) != required or any(not isinstance(layers[key], dict) for key in required):
        raise ReproductionError("layered comparison is incomplete")
    if any(not layer.get("evidence") or "result" not in layer for layer in layers.values()):
        raise ReproductionError("layered comparison evidence is missing")


def validate_release_manifest(manifest: dict[str, Any]) -> None:
    """Require machine-readable release metadata and immutable artifact hashes."""
    required = {"version", "artifacts", "licenses", "model_access", "compute", "commands"}
    if set(manifest) != required or not manifest["version"]:
        raise ReproductionError("release manifest is incomplete")
    if not isinstance(manifest["artifacts"], list) or not manifest["artifacts"]:
        raise ReproductionError("release artifacts are missing")
    for artifact in manifest["artifacts"]:
        if (
            set(artifact) != {"path", "sha256"}
            or not artifact["path"]
            or len(artifact["sha256"]) != 64
        ):
            raise ReproductionError("release artifact hash is invalid")
    if not all(
        isinstance(manifest[key], list) and manifest[key] for key in ("licenses", "commands")
    ):
        raise ReproductionError("release license or command metadata is missing")
    if not isinstance(manifest["model_access"], str) or not manifest["model_access"]:
        raise ReproductionError("model access requirements are missing")
    if not isinstance(manifest["compute"], dict) or not all(
        manifest["compute"].get(key) for key in ("expected_time", "expected_resources")
    ):
        raise ReproductionError("compute expectations are missing")


def validate_submission_bundle(bundle: dict[str, Any]) -> None:
    """Require provenance and resource accounting before public scoring."""
    required = {
        "submission_id",
        "benchmark_version",
        "agent_hash",
        "treatment",
        "sessions",
        "resources",
    }
    if set(bundle) != required or not bundle["submission_id"] or not bundle["benchmark_version"]:
        raise ReproductionError("submission bundle is incomplete")
    if bundle["treatment"] not in {"typed", "monolithic"}:
        raise ReproductionError("submission treatment is invalid")
    if not isinstance(bundle["sessions"], list) or not bundle["sessions"]:
        raise ReproductionError("submission sessions are missing")
    for session in bundle["sessions"]:
        if (
            not isinstance(session, dict)
            or not session.get("session_id")
            or not session.get("provenance")
            or session.get("treatment") != bundle["treatment"]
        ):
            raise ReproductionError("submission session provenance or treatment is incomplete")
    if not isinstance(bundle["resources"], dict) or not all(
        bundle["resources"].get(key) is not None for key in ("tokens", "wall_seconds", "tool_calls")
    ):
        raise ReproductionError("submission resource accounting is incomplete")


def validate_benchmark_version(version: dict[str, Any]) -> None:
    """Require immutable benchmark surfaces and a sealed hidden-evaluation boundary."""
    required = {
        "version",
        "task_hash",
        "scorer_hash",
        "safety_hash",
        "budget_hash",
        "analysis_hash",
        "hidden_sealed",
    }
    if set(version) != required or not version["version"]:
        raise ReproductionError("benchmark version manifest is incomplete")
    if version["hidden_sealed"] is not True:
        raise ReproductionError("hidden evaluation is not sealed")
    hash_keys = required - {"version", "hidden_sealed"}
    if any(not isinstance(version[key], str) or len(version[key]) != 64 for key in hash_keys):
        raise ReproductionError("benchmark version hash is invalid")


def validate_participant_boundary(paths: list[str], forbidden_tokens: set[str]) -> None:
    """Reject participant-visible paths that expose held-out evaluation material."""
    if (
        not paths
        or not forbidden_tokens
        or any(not isinstance(path, str) or not path for path in paths)
    ):
        raise ReproductionError("participant boundary manifest is incomplete")
    lowered_tokens = {token.lower() for token in forbidden_tokens if token}
    if len(lowered_tokens) != len(forbidden_tokens):
        raise ReproductionError("participant boundary token is invalid")
    if any(any(token in path.lower() for token in lowered_tokens) for path in paths):
        raise ReproductionError("participant boundary exposes hidden evaluation")


def validate_rotation_policy(policy: dict[str, Any]) -> None:
    """Require an auditable post-release rotation and contamination response policy."""
    required = {"release_id", "rotate_after_sessions", "heldout_disjoint", "contamination_action"}
    if set(policy) != required or not policy["release_id"]:
        raise ReproductionError("rotation policy is incomplete")
    if not isinstance(policy["rotate_after_sessions"], int) or policy["rotate_after_sessions"] <= 0:
        raise ReproductionError("rotation session threshold is invalid")
    if policy["heldout_disjoint"] is not True:
        raise ReproductionError("held-out rotation is not disjoint")
    if policy["contamination_action"] not in {"quarantine", "retire", "rotate"}:
        raise ReproductionError("contamination action is invalid")
