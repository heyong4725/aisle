"""Build and verify portable Git source evidence for BMK-8 development archives.

Content identity is not publisher authentication, execution attestation, or a
blind-evaluation receipt. Untracked/runtime files are outside this source proof.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path

PROVENANCE = ".aisle-source.json"
SCHEMA = "aisle.source-archive.v1"
OID = re.compile(r"^[0-9a-f]{40}$")


def _oid(kind: str, raw: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).hexdigest()


def _object(record: dict, oid: str, kind: str) -> bytes:
    if not isinstance(oid, str) or not OID.fullmatch(oid):
        raise ValueError("source object identity is invalid")
    try:
        raw = base64.b64decode(record["objects"][oid], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("source object evidence is missing or malformed") from exc
    if _oid(kind, raw) != oid:
        raise ValueError("source object digest mismatch")
    return raw


def _entries(raw: bytes):
    offset = 0
    while offset < len(raw):
        space = raw.find(b" ", offset)
        nul = raw.find(b"\0", space + 1)
        if space < offset or nul < space or nul + 21 > len(raw):
            raise ValueError("malformed source tree")
        mode = raw[offset:space].decode("ascii")
        name = raw[space + 1 : nul].decode("utf-8")
        if not name or name in {".", "..", ".git"} or "/" in name or "\\" in name:
            raise ValueError("unsafe source tree path")
        yield mode, name, raw[nul + 1 : nul + 21].hex()
        offset = nul + 21


def _inventory(record: dict) -> dict[str, tuple[str, str]]:
    if record.get("schema_version") != SCHEMA:
        raise ValueError("unsupported source provenance schema")
    commit = _object(record, record.get("git_sha"), "commit")
    first = commit.split(b"\n", 1)[0]
    if not first.startswith(b"tree "):
        raise ValueError("source commit has no tree")
    files = {}

    def walk(oid, prefix, depth=0):
        if depth > 100:
            raise ValueError("source tree is too deep")
        names = set()
        for mode, name, child in _entries(_object(record, oid, "tree")):
            if name in names:
                raise ValueError("duplicate source tree path")
            names.add(name)
            path = prefix + name
            if mode == "40000":
                walk(child, path + "/", depth + 1)
            elif mode in {"100644", "100755", "120000"}:
                files[path] = (mode, child)
            else:
                raise ValueError("unsupported source tree mode")

    walk(first[5:].decode("ascii"), "")
    if PROVENANCE in files:
        raise ValueError("source tree uses reserved provenance path")
    return files


def verify_source(root: Path, *, expected_commit: str | None = None) -> dict:
    """Verify tracked bytes against Git commit/tree objects, without creating .git."""
    root = root.resolve()
    try:
        path = root / PROVENANCE
        if path.is_symlink():
            raise ValueError("source provenance must not be a symlink")
        record = json.loads(path.read_bytes())
        if not isinstance(record, dict):
            raise ValueError("source provenance must be an object")
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("source provenance is missing or unreadable") from exc
    if expected_commit is not None and record.get("git_sha") != expected_commit:
        raise ValueError("source commit mismatch")
    files = _inventory(record)
    for relative, (mode, oid) in files.items():
        path = root / relative
        if any(
            parent.is_symlink()
            for parent in path.parents
            if parent != root and root in parent.parents
        ):
            raise ValueError(f"source path traverses a symlink: {relative}")
        try:
            observed = path.lstat().st_mode
            if mode == "120000":
                if not stat.S_ISLNK(observed):
                    raise ValueError(f"source mode mismatch: {relative}")
                raw = os.fsencode(os.readlink(path))
            else:
                if not stat.S_ISREG(observed) or bool(observed & 0o111) != (mode == "100755"):
                    raise ValueError(f"source mode mismatch: {relative}")
                raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"source file unavailable: {relative}") from exc
        if _oid("blob", raw) != oid:
            raise ValueError(f"source content mismatch: {relative}")
    return {
        "git_sha": record["git_sha"],
        "files_verified": len(files),
        "source": "verified_git_source_archive",
        "publisher_authenticated": False,
        "provenance_sha256": "sha256:"
        + hashlib.sha256((root / PROVENANCE).read_bytes()).hexdigest(),
    }


def build_archive(root: Path, revision: str, output: Path) -> dict:
    """Export committed blobs plus commit/tree evidence; never copy working-tree edits."""

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    commit = git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    if not OID.fullmatch(commit):
        raise ValueError("only SHA-1 Git repositories are supported")
    record = {"schema_version": SCHEMA, "git_sha": commit, "objects": {}}

    def retain(oid, kind):
        raw = git("cat-file", kind, oid)
        record["objects"][oid] = base64.b64encode(raw).decode()
        return raw

    raw_commit = retain(commit, "commit")

    def trees(oid):
        if oid in record["objects"]:
            return
        for mode, _, child in _entries(retain(oid, "tree")):
            if mode == "40000":
                trees(child)

    trees(raw_commit.split(b"\n", 1)[0][5:].decode())
    files = _inventory(record)
    with output.open("xb") as destination, tarfile.open(fileobj=destination, mode="w") as archive:
        for relative, (mode, oid) in sorted(files.items()):
            raw = git("cat-file", "blob", oid)
            info = tarfile.TarInfo(relative)
            info.mode = 0o755 if mode == "100755" else 0o644
            if mode == "120000":
                info.type = tarfile.SYMTYPE
                info.linkname = os.fsdecode(raw)
                archive.addfile(info)
            else:
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        info = tarfile.TarInfo(PROVENANCE)
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    return {"ok": True, "git_sha": commit, "archive": str(output), "files": len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_archive(args.root, args.revision, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
