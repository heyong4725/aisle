#!/usr/bin/env python3
"""Benchmark v1 version manifest and release audit (BMK-1, BMK-20, BMK-22;
SPEC 540, issue #357).

`--write` regenerates docs/benchmark/v1/version-manifest.json (immutable
hashes of every participant-facing surface the release binds) and
docs/benchmark/v1/release-audit.json (one row per BMK criterion with
`passed`, `failed`, `external_pending`, `dependency_pending`, or
`not_applicable` and evidence links). `--check` recomputes both and fails
on drift. The audit never marks the external-user, blind-isolation, or
public-publication criteria passed from internal evidence (BMK-22).

CON-8: JSON on stdout, logs on stderr, exit 0 iff ok. Deterministic: no
clock, hashes only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V1 = Path("docs/benchmark/v1")
VERSION_ID = "aisle-benchmark-v1-draft"
SURFACES = {
    "participant_contract": V1 / "participant-contract.md",
    "task_distributions": V1 / "task-distributions.json",
    "parity_declaration": V1 / "parity-declaration.json",
    "baselines": V1 / "baselines.json",
    "submission_schema": V1 / "submission.schema.json",
    "leaderboard_schema": V1 / "leaderboard.schema.json",
    "resource_accounting": V1 / "resource-accounting.md",
    "governance": V1 / "governance.md",
    "contamination_rotation": V1 / "contamination-rotation.md",
    "benchmark_card": V1 / "benchmark-card.md",
    "typed_surface_graphs": Path("graphs"),
    "typed_surface_registry": Path("registry/manifests"),
    "safety_boundary_validator": Path("src/aisle/harness/validate.py"),
    "safety_boundary_guard": Path("src/aisle/nodes/budget_guard.py"),
    "safety_boundary_limits": Path("env/limits.toml"),
    "semantic_authorization": Path("src/aisle/harness/semantic_shield.py"),
    "scorer": Path("src/aisle/verifier"),
    "budgets": Path("harness/budget.toml"),
    "hidden_bank_commitment": Path("analysis/fault-bank/commitment.json"),
    "analyzer": Path("src/aisle/harness/benchmark_statistics.py"),
    "submission_validator": Path("src/aisle/harness/reproduction.py"),
    "quickstart": Path("tools/quickstart.py"),
}
OPTIONAL = {"hidden_bank_commitment"}  # tracked only once #348 lands


def _tracked(root: Path, rel: Path) -> list[Path]:
    """Git-tracked files under rel: ignored build output (for example
    graphs/out/ from a dora run) must not move the release hash."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", rel.as_posix()], cwd=root, capture_output=True
    )
    if proc.returncode != 0:
        return sorted(x for x in (root / rel).rglob("*") if x.is_file())
    return sorted(root / p for p in proc.stdout.decode().split("\0") if p)


def sha256_path(path: Path, *, root: Path | None = None) -> str | None:
    if not path.exists():
        return None
    if path.is_file():
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    root = root or ROOT
    digest = hashlib.sha256()
    for p in _tracked(root, path.relative_to(root)):
        if "__pycache__" in p.parts or not p.is_file():
            continue
        digest.update(p.relative_to(path).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(p.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def version_manifest(root: Path) -> dict:
    hashes = {}
    missing = []
    for name, rel in SURFACES.items():
        digest = sha256_path(root / rel, root=root)
        if digest is None and name not in OPTIONAL:
            missing.append(rel.as_posix())
        hashes[name] = {"path": rel.as_posix(), "sha256": digest}
    body = {
        "schema_version": "aisle.benchmark.version-manifest.v1",
        "benchmark_version": VERSION_ID,
        "status": "draft",
        "surfaces": hashes,
        "missing_surfaces": missing,
    }
    body["manifest_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def _exists(root: Path, rel: str) -> bool:
    return (root / rel).exists()


def release_audit(root: Path, manifest: dict) -> dict:
    """BMK-22: one row per criterion. Internal evidence can pass a document
    or tool criterion; it can never pass the external-user, blind-isolation,
    or public-publication criteria."""
    v1 = V1.as_posix()
    rows = {
        "BMK-1": (
            "passed" if not manifest["missing_surfaces"] else "failed",
            [f"{v1}/version-manifest.json"],
            "version manifest binds every listed surface by hash",
        ),
        "BMK-2": (
            "passed",
            [f"{v1}/participant-contract.md"],
            "contract enumerates grants, forbids the rest, documents refusal",
        ),
        "BMK-3": (
            "dependency_pending",
            [f"{v1}/task-distributions.json"],
            "public dev and qualification pinned; final task ids await #346 (BND-1)",
        ),
        "BMK-4": (
            "dependency_pending",
            [f"{v1}/parity-declaration.json"],
            "no monolithic surface exists (#344)",
        ),
        "BMK-5": (
            "dependency_pending",
            [f"{v1}/baselines.json", "tools/agent_adapters.py"],
            "adapters and prompts exist for two hosted agents; zero of four "
            "baseline cells run; monolithic treatment missing",
        ),
        "BMK-6": (
            "dependency_pending",
            ["graphs/expert_t1_l2.yaml", "graphs/expert_t2.yaml", "tools/local_baseline.py"],
            "typed expert artifacts and deterministic fixture exist; monolithic experts missing",
        ),
        "BMK-7": (
            "passed" if _exists(root, "tools/quickstart.py") else "failed",
            ["tools/quickstart.py"],
            "non-interactive clean-clone command with CON-8 record exists; "
            "fresh-clone execution is BMK-8",
        ),
        "BMK-8": (
            "external_pending",
            [],
            "must be exercised from a fresh clone on every release platform with "
            "measured time and storage; not done",
        ),
        "BMK-9": (
            "passed",
            [f"{v1}/task-distributions.json"],
            "development_public role and the same validator are declared",
        ),
        "BMK-10": (
            "dependency_pending",
            ["specs/420-treatment-integrity.md"],
            "blind sandbox and independent controller depend on #353 confinement and #348 sealing",
        ),
        "BMK-11": (
            "external_pending",
            [],
            "isolation audit with canaries in every private class not performed",
        ),
        "BMK-12": (
            "dependency_pending",
            ["analysis/fault-bank/commitment.json"],
            "bank commitment exists in draft; roles, access log, and qualification split pending",
        ),
        "BMK-13": (
            "passed",
            [f"{v1}/submission.schema.json"],
            "submission schema retains every required field and forbids a participant score",
        ),
        "BMK-14": (
            "passed",
            ["src/aisle/harness/reproduction.py", "tests/unit/test_benchmark_release.py"],
            "deterministic fail-closed validation of the schema; leaked-marker and "
            "budget checks are unit-level",
        ),
        "BMK-15": (
            "dependency_pending",
            [],
            "evaluator receipt signing and private-hash binding await the blind path",
        ),
        "BMK-16": (
            "passed",
            [f"{v1}/leaderboard.schema.json"],
            "report schema carries units, denominators, effects, exclusions, "
            "integrity, safety, resources, claim status; no ranking scalar",
        ),
        "BMK-17": (
            "passed",
            [f"{v1}/resource-accounting.md"],
            "token, cache, pricing, retry, parallel, utilization, and amortization rules defined",
        ),
        "BMK-18": (
            "passed",
            [f"{v1}/governance.md"],
            "maintainers, compatibility, migration, leak response, appeals, "
            "withdrawal, errata defined",
        ),
        "BMK-19": (
            "passed",
            [f"{v1}/contamination-rotation.md"],
            "release dates, cutoffs, disclosures, quarantine, rotation, comparability defined",
        ),
        "BMK-20": (
            "failed",
            [f"{v1}/benchmark-card.md"],
            "no LICENSE or CITATION file; license selection is an owner decision; no DOI (#355)",
        ),
        "BMK-21": (
            "external_pending",
            [],
            "no external person or group has completed the clean-clone cell",
        ),
        "BMK-22": ("passed", [f"{v1}/release-audit.json"], "this report"),
    }
    audit_rows = [
        {"criterion": k, "status": s, "evidence": e, "note": n} for k, (s, e, n) in rows.items()
    ]
    return {
        "schema_version": "aisle.benchmark.release-audit.v1",
        "benchmark_version": VERSION_ID,
        "manifest_sha256": manifest["manifest_sha256"],
        "criteria": audit_rows,
        "counts": {
            status: sum(1 for r in audit_rows if r["status"] == status)
            for status in (
                "passed",
                "failed",
                "external_pending",
                "dependency_pending",
                "not_applicable",
            )
        },
        "release_ready": False,
        "wording": "#357 stays open until every applicable criterion has "
        "reviewable evidence; internal dry runs never pass the external criteria",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = version_manifest(root)
    audit = release_audit(root, manifest)
    manifest_path = root / V1 / "version-manifest.json"
    audit_path = root / V1 / "release-audit.json"
    ok = not manifest["missing_surfaces"]
    if args.write:
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
        reason = "written"
    else:
        drift = []
        for path, current in ((manifest_path, manifest), (audit_path, audit)):
            if not path.exists():
                drift.append(f"missing {path.relative_to(root)}")
            elif json.loads(path.read_text()) != current:
                drift.append(f"stale {path.relative_to(root)}")
        ok = ok and not drift
        reason = "current" if not drift else "; ".join(drift)
    report = {
        "ok": ok,
        "benchmark_version": VERSION_ID,
        "manifest_sha256": manifest["manifest_sha256"],
        "missing_surfaces": manifest["missing_surfaces"],
        "audit_counts": audit["counts"],
        "release_ready": audit["release_ready"],
        "reason": reason,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
