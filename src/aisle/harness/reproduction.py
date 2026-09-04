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
