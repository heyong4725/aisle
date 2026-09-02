"""Independent frozen-view construction and synthetic TRT-12 audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "aisle.independent-view.v1"
AUDIT_SCHEMA_VERSION = "aisle.contamination-capability.v1"
EVIDENCE_CLASS = "synthetic_unscored_contamination"
_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SOURCE_CLASSES = (
    "predecessor_cache",
    "predecessor_home",
    "predecessor_worktree",
    "prior_analysis",
    "prior_deliverable_ref",
    "prior_transcript",
)


class ContaminationError(RuntimeError):
    """An independent view or its contamination audit is unusable."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_binary() -> Path:
    candidate = shutil.which("git")
    if candidate is None:
        raise ContaminationError("required Git executable is unavailable")
    path = Path(candidate).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ContaminationError("required Git executable is unresolved")
    return path


def _git_env(git: Path) -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": str(git.parent),
    }


def _git(repository: Path, args: list[str], *, binary: bool = False) -> bytes | str:
    git = _git_binary()
    try:
        result = subprocess.run(
            [str(git), *args],
            cwd=repository,
            env=_git_env(git),
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContaminationError(f"Git controller command failed: {args[0]}: {exc}") from exc
    if result.returncode != 0:
        raise ContaminationError(
            f"Git controller command refused: {args[0]} rc={result.returncode} "
            f"stderr_sha256={_sha256(result.stderr)}"
        )
    if binary:
        return result.stdout
    try:
        return result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ContaminationError(f"Git controller output is not ASCII: {args[0]}") from exc


def _validated_allowlist(allowlist: Any) -> list[str]:
    if (
        not isinstance(allowlist, list)
        or not allowlist
        or not all(isinstance(path, str) and path for path in allowlist)
    ):
        raise ContaminationError("visible allowlist must be a non-empty string list")
    if allowlist != sorted(allowlist) or len(allowlist) != len(set(allowlist)):
        raise ContaminationError("visible allowlist must be sorted without duplicates")
    for path in allowlist:
        pure = PurePosixPath(path)
        if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
            raise ContaminationError(f"visible allowlist contains an unsafe path: {path}")
    return allowlist


def _baseline(repository: Path, commit: str) -> tuple[str, str]:
    if not isinstance(commit, str) or not _OID_RE.fullmatch(commit):
        raise ContaminationError("baseline must be a full commit object id")
    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise ContaminationError("repository is unresolved")
    try:
        resolved = _git(repository, ["rev-parse", "--verify", f"{commit}^{{commit}}"])
    except ContaminationError as exc:
        raise ContaminationError(f"unknown baseline commit: {commit}") from exc
    if resolved != commit:
        raise ContaminationError("baseline did not resolve to the exact supplied commit")
    tree = _git(repository, ["rev-parse", f"{commit}^{{tree}}"])
    return commit, str(tree)


def build_independent_view(
    repository: Path,
    baseline_commit: str,
    visible_allowlist: list[str],
    destination: Path,
) -> dict:
    """Export only allowlisted blobs from one exact commit into a fresh tree."""
    destination = Path(destination)
    if destination.exists():
        raise ContaminationError(f"independent view destination already exists: {destination}")
    allowlist = _validated_allowlist(visible_allowlist)
    commit, tree = _baseline(Path(repository), baseline_commit)

    prepared: list[tuple[str, str, bytes]] = []
    for path in allowlist:
        listing = _git(Path(repository), ["ls-tree", "-z", commit, "--", path], binary=True)
        assert isinstance(listing, bytes)
        rows = [row for row in listing.split(b"\0") if row]
        if len(rows) != 1 or b"\t" not in rows[0]:
            raise ContaminationError(f"allowlisted baseline path is unresolved: {path}")
        metadata, raw_path = rows[0].split(b"\t", 1)
        try:
            mode, object_type, _ = metadata.decode("ascii").split()
            listed_path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ContaminationError(
                f"invalid Git tree entry for allowlisted path: {path}"
            ) from exc
        if listed_path != path or object_type != "blob" or mode not in {"100644", "100755"}:
            raise ContaminationError(f"allowlisted baseline path is not a regular file: {path}")
        payload = _git(Path(repository), ["show", f"{commit}:{path}"], binary=True)
        assert isinstance(payload, bytes)
        prepared.append((path, mode, payload))

    destination.mkdir(parents=True, exist_ok=False)
    visible_files: list[dict[str, str]] = []
    for path, mode, payload in prepared:
        target = destination.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(0o755 if mode == "100755" else 0o644)
        visible_files.append({"mode": mode, "path": path, "sha256": _sha256(payload)})

    git = _git_binary()
    record = {
        "baseline_commit": commit,
        "baseline_tree": tree,
        "builder_sha256": _sha256(Path(__file__).read_bytes()),
        "git_binary_sha256": _sha256(git.read_bytes()),
        "schema_version": SCHEMA_VERSION,
        "visible_allowlist": allowlist,
        "visible_files": visible_files,
    }
    record["immutable_id"] = f"sha256:{_sha256(_canonical_bytes(record))}"
    return record


def _commit(repository: Path, message: str) -> str:
    _git(repository, ["add", "--all"])
    _git(
        repository,
        [
            "-c",
            "user.name=AISLE synthetic controller",
            "-c",
            "user.email=synthetic-controller@invalid",
            "commit",
            "--quiet",
            "-m",
            message,
        ],
    )
    return str(_git(repository, ["rev-parse", "HEAD"]))


def run_contamination_capability_audit() -> dict:
    """Demonstrate exclusion of six synthetic same-experiment channels."""
    with tempfile.TemporaryDirectory(prefix="aisle-contamination-") as temporary:
        root = Path(temporary).resolve()
        repository = root / "repository"
        repository.mkdir()
        _git(repository, ["init", "--quiet"])
        (repository / "AGENTS.md").write_text("synthetic frozen contract\n")
        (repository / "src").mkdir()
        (repository / "src" / "worker.py").write_text("print('synthetic baseline')\n")
        baseline = _commit(repository, "synthetic frozen baseline")

        sentinel = b"SYNTHETIC-CONTAMINATION-SENTINEL-5FD2\n"
        filesystem_sources: dict[str, tuple[str, Path]] = {}
        for source_class, mechanism in (
            ("predecessor_cache", "filesystem_cache"),
            ("predecessor_home", "filesystem_home"),
            ("prior_transcript", "filesystem_transcript"),
        ):
            path = root / "predecessor-material" / source_class / "sentinel.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(sentinel)
            filesystem_sources[source_class] = (mechanism, path)
        analysis = repository / "analysis"
        analysis.mkdir()
        (analysis / "same-experiment.json").write_bytes(sentinel)
        later_commit = _commit(repository, "synthetic later analysis")
        predecessor_worktree = root / "predecessor-worktree"
        _git(
            repository,
            [
                "worktree",
                "add",
                "--quiet",
                "--detach",
                str(predecessor_worktree),
                later_commit,
            ],
        )
        _git(
            repository,
            ["update-ref", "refs/campaign/prior-deliverable", later_commit],
        )

        destination = root / "later-arm-view"
        view = build_independent_view(
            repository,
            baseline,
            ["AGENTS.md", "src/worker.py"],
            destination,
        )
        source_payloads: dict[str, tuple[str, bytes]] = {
            source_class: (mechanism, path.read_bytes())
            for source_class, (mechanism, path) in filesystem_sources.items()
        }
        source_payloads.update(
            {
                "predecessor_worktree": (
                    "git_worktree",
                    (predecessor_worktree / "analysis" / "same-experiment.json").read_bytes(),
                ),
                "prior_analysis": (
                    "later_commit_path",
                    (analysis / "same-experiment.json").read_bytes(),
                ),
                "prior_deliverable_ref": (
                    "git_ref",
                    _git(
                        repository,
                        [
                            "show",
                            "refs/campaign/prior-deliverable:analysis/same-experiment.json",
                        ],
                        binary=True,
                    ),
                ),
            }
        )
        sources = []
        for source_class, (mechanism, payload) in sorted(source_payloads.items()):
            assert isinstance(payload, bytes)
            sources.append(
                {
                    "baseline_exposed": sentinel in payload,
                    "mechanism": mechanism,
                    "payload_sha256": _sha256(payload),
                    "source_class": source_class,
                }
            )
        view_exposures = sum(
            sentinel in path.read_bytes() for path in destination.rglob("*") if path.is_file()
        )
        source_exposures = sum(row["baseline_exposed"] for row in sources)
        capability_pass = (
            source_exposures == len(_SOURCE_CLASSES)
            and view_exposures == 0
            and baseline != later_commit
            and not (destination / ".git").exists()
        )
        recorded_at = datetime.now(UTC)
        return {
            "baseline_commit": baseline,
            "capability_pass": capability_pass,
            "confirmatory_ready": False,
            "evidence_class": EVIDENCE_CLASS,
            "later_commit": later_commit,
            "limitations": [
                "synthetic sentinels only; no confirmatory agent sessions",
                "view construction is not yet wired into Claude or Codex launchers",
                "dependency caches and credentials require separate TRT-7 enforcement",
            ],
            "recorded_at": recorded_at.isoformat(),
            "schema_version": AUDIT_SCHEMA_VERSION,
            "session_id": f"contamination-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}-"
            f"{baseline[:12]}",
            "sources": sources,
            "summary": {
                "contamination_sources": len(_SOURCE_CLASSES),
                "source_baselines_exposed": source_exposures,
                "view_exposures": view_exposures,
            },
            "view": view,
        }


def write_contamination_capability_audit(output: Path) -> dict:
    """Retain one non-overwriting synthetic contamination audit."""
    output = Path(output)
    if output.exists():
        raise ContaminationError(f"contamination audit already exists: {output}")
    report = run_contamination_capability_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise ContaminationError(f"contamination audit already exists: {output}") from exc
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the synthetic TRT-12 audit")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit-synthetic")
    audit.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = write_contamination_capability_audit(args.output)
    except ContaminationError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "capability_pass": report["capability_pass"],
                "confirmatory_ready": report["confirmatory_ready"],
                "evidence_class": report["evidence_class"],
                "ok": report["capability_pass"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["capability_pass"] else 3


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
