"""Capability registry CLI: lint and search (SPEC 050 CAP-3/CAP-4, CON-8).

lint: validate every manifest in registry/manifests/ against
capability.schema.json (CAP-1), the closed schema vocabulary in
registry/schema/schemas.toml (CAP-2), and the CAP-6 eval rule.
search: return manifests matching --provides (and optionally --embodiment)
as JSON. Single JSON object on stdout, logs on stderr, exit 0 iff ok.
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

import yaml

from aisle.harness.common import DEFAULT_ROOT, emit_report

# CAP-6: the two sim drivers ship eval=null until their M0 evalcards are
# generated from the SPEC 010 acceptance runs (ADR-3) — lint warns instead
# of erroring.
# ADR-3's pending-evalcard carve-out is RETIRED (T08): the TC-A1..A3
# acceptance runs pass, the evalcards are generated from them, and the
# expert graph must survive normal validation (HAR-2 runs it without
# --allow-unproven). The set stays as an empty tombstone the T10 gate
# asserts on.
PENDING_M0_EVALCARDS: set[str] = set()


def load_manifests(root: Path) -> tuple[list[tuple[Path, dict]], list[dict]]:
    """Parse every manifest; a missing or empty manifests dir is an error,
    never a silent empty registry."""
    manifests_dir = root / "registry" / "manifests"
    if not manifests_dir.is_dir():
        return [], [{"manifest": "(registry)", "message": f"{manifests_dir} not found"}]
    manifests: list[tuple[Path, dict]] = []
    errors: list[dict] = []
    for path in sorted(manifests_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            errors.append({"manifest": path.name, "message": f"unparseable YAML: {exc}"})
            continue
        if not isinstance(data, dict):
            errors.append({"manifest": path.name, "message": "manifest is not a mapping"})
            continue
        manifests.append((path, data))
    if not manifests and not errors:
        errors.append({"manifest": "(registry)", "message": f"no manifests in {manifests_dir}"})
    return manifests, errors


def load_vocabulary(root: Path) -> dict:
    """The CAP-2 closed schema vocabulary; raises OSError/TOMLDecodeError."""
    with open(root / "registry" / "schema" / "schemas.toml", "rb") as f:
        return tomllib.load(f)


def load_capability_schema(root: Path) -> dict:
    """The CAP-1 manifest JSON Schema; raises OSError/JSONDecodeError."""
    return json.loads((root / "registry" / "schema" / "capability.schema.json").read_text())


def manifest_schema_errors(schema: dict, manifest: dict) -> list[str]:
    """JSON-path-prefixed CAP-1 schema violations for one manifest."""
    # imported lazily to keep module import light for non-validating callers
    from jsonschema import Draft202012Validator

    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '(root)'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(manifest)
    ]


def lint(root: Path) -> dict:
    try:
        schema = load_capability_schema(root)
        vocabulary_entries = load_vocabulary(root)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return {
            "ok": False,
            "checked": 0,
            "errors": [{"manifest": "(schema)", "message": f"cannot load schema files: {exc}"}],
            "warnings": [],
        }

    manifests, errors = load_manifests(root)
    vocabulary = set(vocabulary_entries)
    for name, entry in vocabulary_entries.items():
        well_formed = (
            isinstance(entry, dict)
            and set(entry) == {"arrow", "shape"}
            and all(isinstance(v, str) for v in entry.values())
        )
        if not well_formed:
            errors.append(
                {
                    "manifest": "(schema)",
                    "message": f"schemas.toml entry {name!r} must map exactly "
                    "{arrow, shape} to strings (CAP-2)",
                }
            )
    warnings: list[dict] = []
    for path, manifest in manifests:
        for message in manifest_schema_errors(schema, manifest):
            errors.append({"manifest": path.name, "message": message})
        # filenames are unique per directory, so id == stem also implies
        # registry-wide id uniqueness
        if manifest.get("id") != path.stem:
            errors.append(
                {"manifest": path.name, "message": f"id {manifest.get('id')!r} != filename stem"}
            )
        for direction in ("inputs", "outputs"):
            ports = manifest.get(direction)
            if not isinstance(ports, dict):
                continue
            for port, spec in ports.items():
                schema_name = spec.get("schema") if isinstance(spec, dict) else None
                if schema_name is not None and schema_name not in vocabulary:
                    errors.append(
                        {
                            "manifest": path.name,
                            "message": f"{direction}/{port}: schema {schema_name!r} not in "
                            "registry/schema/schemas.toml (CAP-2)",
                        }
                    )
        if "eval" in manifest and manifest["eval"] is None:
            origin_hub = manifest.get("origin") == "hub"
            if not origin_hub or manifest.get("safety_class") == "motion":
                pending = origin_hub and manifest.get("id") in PENDING_M0_EVALCARDS
                suffix = " — pending M0 evalcard (ADR-3)" if pending else ""
                (warnings if pending else errors).append(
                    {
                        "manifest": path.name,
                        "message": "eval may be null only while origin=hub and "
                        f"safety_class!=motion (CAP-6){suffix}",
                    }
                )

    return {
        "ok": not errors,
        "checked": len(manifests),
        "errors": errors,
        "warnings": warnings,
    }


def search(root: Path, provides: str, embodiment: str | None) -> dict:
    manifests, errors = load_manifests(root)
    if errors:
        return {"ok": False, "matches": [], "errors": errors}

    def matches(manifest: dict) -> bool:
        provided = manifest.get("provides")
        if not isinstance(provided, list) or provides not in provided:
            return False
        if embodiment is None:
            return True
        arms = manifest.get("embodiment")
        arm_list = arms.get("arm") if isinstance(arms, dict) else None
        return isinstance(arm_list, list) and embodiment in arm_list

    # load_manifests iterates sorted filenames, and lint enforces id ==
    # filename stem, so this is already id order for any lint-clean registry
    return {"ok": True, "matches": [manifest for _, manifest in manifests if matches(manifest)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    lint_parser = subparsers.add_parser("lint", help="validate every manifest")
    lint_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    search_parser = subparsers.add_parser("search", help="find manifests by capability")
    search_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    search_parser.add_argument("--provides", required=True)
    search_parser.add_argument("--embodiment")
    args = parser.parse_args()

    if args.command == "lint":
        report = lint(args.root)
    else:
        report = search(args.root, args.provides, args.embodiment)

    return emit_report(
        report, lambda level, e: f"{args.command} {level}: {e['manifest']}: {e['message']}"
    )


if __name__ == "__main__":
    sys.exit(main())
