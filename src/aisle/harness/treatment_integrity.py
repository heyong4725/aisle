"""Fail-closed treatment-manifest construction for SPEC 420.

This module is deliberately session-independent: the campaign controller builds
and verifies the manifest before it starts an agent-side process.  Sealed-view
execution and postflight auditing are separate SPEC 420 gates.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "aisle.treatment.v1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SECRET_VALUE_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,}|"
    r"\bghp_[A-Za-z0-9]{8,}|\bgithub_pat_[A-Za-z0-9_]{8,})"
)
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "api_token",
    "authorization",
    "client_secret",
    "credential_bytes",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
_PLACEHOLDERS = {"", "unknown", "tbd", "todo", "placeholder", "latest", "unset"}

_REQUIRED_PATHS = (
    "schema_version",
    "repository.commit",
    "repository.tree",
    "repository.visible_allowlist",
    "model.requested_identity",
    "model.served_identity",
    "agent.kind",
    "agent.cli_revision",
    "agent.cli_binary_sha256",
    "sampling",
    "prompts.system_sha256",
    "prompts.research_contract_sha256",
    "runtime_binaries",
    "environment.fingerprint_sha256",
    "environment.simulator_backend",
    "environment.platform",
    "policy.approval",
    "policy.tool_policy_sha256",
    "policy.network",
    "policy.allowed_external_tools",
    "state.credential_source_class",
    "state.credential_provenance",
    "state.credential_policy_sha256",
    "state.home_baseline_sha256",
    "state.config_baseline_sha256",
    "state.cache_baseline_sha256",
    "state.environment_baseline_sha256",
    "prior_context.findings_sha256",
    "prior_context.skills_sha256",
    "prior_context.context_sha256",
    "budget.unit",
    "budget.ceiling",
    "assignment.temporal_block",
    "assignment.arm",
    "assignment.randomization_seed_commitment",
    "host_load.sampling_rule_sha256",
    "host_load.baseline",
    "confinement.adapter_binary_sha256",
    "confinement.profile_sha256",
    "confinement.policy_sha256",
)

_HASH_PATHS = (
    "agent.cli_binary_sha256",
    "prompts.system_sha256",
    "prompts.research_contract_sha256",
    "environment.fingerprint_sha256",
    "policy.tool_policy_sha256",
    "state.credential_policy_sha256",
    "state.home_baseline_sha256",
    "state.config_baseline_sha256",
    "state.cache_baseline_sha256",
    "state.environment_baseline_sha256",
    "prior_context.findings_sha256",
    "prior_context.skills_sha256",
    "prior_context.context_sha256",
    "assignment.randomization_seed_commitment",
    "host_load.sampling_rule_sha256",
    "confinement.adapter_binary_sha256",
    "confinement.profile_sha256",
    "confinement.policy_sha256",
)


class ManifestError(ValueError):
    """The controller cannot prove a complete, non-secret treatment identity."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"treatment is not canonical JSON: {exc}") from exc
    return rendered.encode()


def _get(value: dict, dotted_path: str) -> Any:
    cursor: Any = value
    for component in dotted_path.split("."):
        if not isinstance(cursor, dict) or component not in cursor:
            raise ManifestError(f"required treatment field is absent: {dotted_path}")
        cursor = cursor[component]
    return cursor


def _validate_required(candidate: dict) -> None:
    for path in _REQUIRED_PATHS:
        value = _get(candidate, path)
        if value is None:
            raise ManifestError(f"required treatment field is unresolved: {path}")
        if isinstance(value, str) and value.strip().lower() in _PLACEHOLDERS:
            raise ManifestError(f"required treatment field is ambiguous: {path}")

    if candidate["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {candidate['schema_version']!r}"
        )

    for path in ("repository.commit", "repository.tree"):
        value = _get(candidate, path)
        if not isinstance(value, str) or not _GIT_OID_RE.fullmatch(value):
            raise ManifestError(f"{path} must be an exact Git object id")

    for path in _HASH_PATHS:
        value = _get(candidate, path)
        if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
            raise ManifestError(f"{path} must be an exact lowercase SHA-256")

    ceiling = candidate["budget"]["ceiling"]
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)) or ceiling <= 0:
        raise ManifestError("budget.ceiling must be a positive number")

    sampling = candidate["sampling"]
    if not isinstance(sampling, dict) or not sampling:
        raise ManifestError("sampling must be a non-empty mapping")
    _validate_finite_numbers(sampling, "sampling")

    baseline = candidate["host_load"]["baseline"]
    if not isinstance(baseline, dict) or not baseline:
        raise ManifestError("host_load.baseline must be a non-empty mapping")
    _validate_finite_numbers(baseline, "host_load.baseline")

    tools = candidate["policy"]["allowed_external_tools"]
    if not isinstance(tools, list) or not all(isinstance(item, str) and item for item in tools):
        raise ManifestError("policy.allowed_external_tools must be a resolved string list")
    if len(tools) != len(set(tools)):
        raise ManifestError("policy.allowed_external_tools contains a duplicate")

    binaries = candidate["runtime_binaries"]
    if not isinstance(binaries, list) or not binaries:
        raise ManifestError("runtime_binaries must be a non-empty list")
    names: list[str] = []
    for index, binary in enumerate(binaries):
        if not isinstance(binary, dict) or set(binary) != {"name", "sha256"}:
            raise ManifestError(f"runtime_binaries[{index}] must contain only name and sha256")
        name = binary["name"]
        digest = binary["sha256"]
        if not isinstance(name, str) or name.strip().lower() in _PLACEHOLDERS:
            raise ManifestError(f"runtime_binaries[{index}].name is unresolved")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise ManifestError(f"runtime_binaries[{index}].sha256 must be an exact SHA-256")
        names.append(name)
    if len(names) != len(set(names)):
        raise ManifestError("runtime_binaries contains a duplicate name")


def _validate_finite_numbers(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ManifestError(f"{path} contains a non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_finite_numbers(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_finite_numbers(child, f"{path}[{index}]")


def _reject_secrets(value: Any, path: str = "treatment") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if lowered in _SECRET_KEYS or lowered.endswith(("_password", "_secret", "_token")):
                raise ManifestError(
                    f"secret-bearing key is forbidden in retained evidence: {path}.{key}"
                )
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        raise ManifestError(f"probable secret bytes are forbidden in retained evidence: {path}")


def _visible_files(allowlist: Any, root: Path) -> list[dict[str, str]]:
    if not isinstance(allowlist, list) or not allowlist:
        raise ManifestError("repository.visible_allowlist must be a non-empty string list")
    if not all(isinstance(item, str) and item for item in allowlist):
        raise ManifestError("repository.visible_allowlist must contain only non-empty paths")
    if allowlist != sorted(allowlist) or len(allowlist) != len(set(allowlist)):
        raise ManifestError("repository.visible_allowlist must be sorted and contain no duplicate")

    resolved_root = root.resolve(strict=True)
    rows: list[dict[str, str]] = []
    for raw_path in allowlist:
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
            raise ManifestError(f"repository.visible_allowlist contains unsafe path: {raw_path}")
        path = root.joinpath(*pure.parts)
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ManifestError(
                f"repository.visible_allowlist path is unreadable: {raw_path}: {exc}"
            ) from exc
        if (
            path.is_symlink()
            or not resolved.is_relative_to(resolved_root)
            or not resolved.is_file()
        ):
            raise ManifestError(
                f"repository.visible_allowlist path is not a contained file: {raw_path}"
            )
        rows.append({"path": raw_path, "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()})
    return rows


def _content_id(manifest_without_id: dict) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(manifest_without_id)).hexdigest()}"


def create_treatment_manifest(candidate: dict, root: Path) -> dict:
    """Validate and content-address one complete controller-side preflight."""
    if not isinstance(candidate, dict):
        raise ManifestError("treatment candidate must be a mapping")
    manifest = copy.deepcopy(candidate)
    if "immutable_id" in manifest:
        raise ManifestError("immutable_id is derived and must not be supplied")
    repository = manifest.get("repository")
    if isinstance(repository, dict) and "visible_files" in repository:
        raise ManifestError("repository.visible_files is derived and must not be supplied")

    _reject_secrets(manifest)
    _validate_required(manifest)
    manifest["repository"]["visible_files"] = _visible_files(
        manifest["repository"]["visible_allowlist"], Path(root)
    )
    manifest["immutable_id"] = _content_id(manifest)
    return manifest


def verify_treatment_manifest(manifest: dict, root: Path) -> dict:
    """Recompute visible-file and content identities for a retained manifest."""
    if not isinstance(manifest, dict):
        raise ManifestError("retained treatment manifest must be a mapping")
    retained = copy.deepcopy(manifest)
    immutable_id = retained.pop("immutable_id", None)
    if not isinstance(immutable_id, str) or not immutable_id.startswith("sha256:"):
        raise ManifestError("immutable_id is absent or malformed")
    repository = retained.get("repository")
    if not isinstance(repository, dict):
        raise ManifestError("required treatment field is absent: repository")
    recorded_files = repository.pop("visible_files", None)
    current_files = _visible_files(repository.get("visible_allowlist"), Path(root))
    if recorded_files != current_files:
        raise ManifestError("visible file drift detected")

    rebuilt = create_treatment_manifest(retained, Path(root))
    if immutable_id != rebuilt["immutable_id"]:
        raise ManifestError("immutable_id does not match treatment content")
    if manifest != rebuilt:
        raise ManifestError("retained treatment manifest is not canonical")
    return copy.deepcopy(manifest)


def write_treatment_manifest(candidate: dict, root: Path, output: Path) -> dict:
    """Create one retained manifest without overwriting prior evidence."""
    manifest = create_treatment_manifest(candidate, root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(manifest, allow_nan=False, indent=2, sort_keys=True) + "\n"
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ManifestError(f"treatment manifest already exists: {output}") from exc
    return manifest


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"unreadable {label} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{label} JSON must contain one object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPEC 420 treatment-manifest preflight")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="validate and retain a preflight manifest")
    create.add_argument("--candidate", type=Path, required=True)
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify a retained manifest and visible tree")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the controller-facing JSON CLI."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            candidate = _read_json_object(args.candidate, "candidate")
            manifest = write_treatment_manifest(candidate, args.root, args.output)
            report = {
                "immutable_id": manifest["immutable_id"],
                "ok": True,
                "output": str(args.output),
            }
        else:
            manifest = _read_json_object(args.manifest, "manifest")
            verified = verify_treatment_manifest(manifest, args.root)
            report = {"immutable_id": verified["immutable_id"], "ok": True}
    except ManifestError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
