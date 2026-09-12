"""Prepare a public pinned identity cache for separately admitted L2 workers.

No downloads or permission grants occur here. The caller must include the
resulting tree in the runtime receipt and read-only policy, and bind the returned
environment in the participant graph before snapshot/admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def prepare_cache(root: Path, source: Path, output: Path) -> dict:
    """BND-2/MON-6/MON-8: copy only hash-verified identity files into a fresh cache."""
    root, source, output = (Path(p).absolute() for p in (root, source, output))
    if any(p.resolve() != p for p in (root, source, output)) or not source.is_dir():
        raise ValueError("model cache paths must be canonical")
    if output.exists() or any(
        output.is_relative_to(p) or p.is_relative_to(output) for p in (root, source)
    ):
        raise ValueError("model cache requires a fresh disjoint output")
    lock_path = root / "src/aisle/verifier/models.lock"
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    if lock.get("lock_version") != 1:
        raise ValueError("unsupported model lock")
    entry = lock["models"]["identity"]
    repo, revision, pins = entry["repo"], entry["revision"], entry["files_sha256"]
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
        or type(pins) is not dict
        or not pins
    ):
        raise ValueError("invalid pinned identity model declaration")
    if source.name != revision or source.parent.name != "snapshots":
        raise ValueError("source must be the pinned Hugging Face cache snapshot")
    tree_source = source.parent.parent / "trees" / f"{revision}.json"
    tree_bytes = tree_source.read_bytes()
    tree = json.loads(tree_bytes)
    if tree.get("format_version") != 1 or type(tree.get("files")) is not dict:
        raise ValueError("unsupported model tree metadata")
    output.mkdir(parents=True, exist_ok=False)
    storage = output / "hub" / ("models--" + repo.replace("/", "--"))
    snapshot = storage / "snapshots" / revision
    copied = {}
    selected_tree = {}
    for name, expected in sorted(pins.items()):
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or any(c in name for c in "*?[]\\")
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise ValueError("invalid pinned model file declaration")
        matches = list(source.rglob(name))
        if len(matches) != 1 or not matches[0].is_file():
            raise ValueError("pinned identity file is missing or ambiguous")
        target = snapshot / matches[0].relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with matches[0].open("rb") as incoming, target.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                outgoing.write(chunk)
        target.chmod(0o444)
        if digest.hexdigest() != expected:
            raise ValueError("pinned identity file digest differs")
        relative_name = target.relative_to(snapshot).as_posix()
        metadata = tree["files"].get(relative_name)
        size = target.stat().st_size
        if (
            type(metadata) is not dict
            or type(metadata.get("size")) is not int
            or metadata["size"] != size
            or not isinstance(metadata.get("blob_id"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", metadata["blob_id"])
            or (
                "lfs_sha256" in metadata
                and (metadata["lfs_sha256"] != expected or metadata.get("lfs_size") != size)
            )
        ):
            raise ValueError("model tree metadata differs from verified snapshot")
        # Keep only public download fields needed by the pinned Hub cache reader.
        # Xet routing and unrelated repository files are unnecessary offline.
        selected_tree[relative_name] = {"size": size, "blob_id": metadata["blob_id"]}
        if "lfs_sha256" in metadata:
            selected_tree[relative_name].update(lfs_sha256=expected, lfs_size=size)
        copied[str(target.relative_to(output))] = expected
    if lock_path.read_bytes() != lock_bytes or tree_source.read_bytes() != tree_bytes:
        raise ValueError("model lock or tree metadata changed during cache preparation")
    tree_target = storage / "trees" / f"{revision}.json"
    tree_target.parent.mkdir()
    tree_target.write_text(
        json.dumps({"format_version": 1, "files": selected_tree}, sort_keys=True) + "\n"
    )
    tree_target.chmod(0o444)
    copied[str(tree_target.relative_to(output))] = hashlib.sha256(
        tree_target.read_bytes()
    ).hexdigest()
    return {
        "schema_version": "aisle.pilot-model-cache.v1",
        "ok": True,
        "study_collection_authorized": False,
        "cache_root": str(output),
        "model_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "files": copied,
        "worker_environment": {
            "HF_HOME": str(output),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main() -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    try:
        args = parser.parse_args()
        report = prepare_cache(args.root, args.source_snapshot, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {"ok": False, "error": str(exc)}
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
