"""Content receipts for controller-declared language runtime trees.

These receipts bind files, not OS authority or package suitability. Callers must
also verify that launch read grants and interpreter paths use the bound trees.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


class RuntimeDrift(ValueError):
    """A runtime tree cannot be reconciled with its retained inventory."""


def capture_runtime(roots):
    """Inventory disjoint canonical trees, refusing links outside the closed set."""
    roots = sorted(Path(p).absolute() for p in roots)
    if not roots:
        raise RuntimeDrift("runtime roots are absent")
    for i, root in enumerate(roots):
        if root == Path("/") or root.resolve() != root or not root.is_dir():
            raise RuntimeDrift("runtime root is redirected, missing or unrestricted")
        if any(root.is_relative_to(other) or other.is_relative_to(root) for other in roots[:i]):
            raise RuntimeDrift("runtime roots overlap")
    inventory = {}
    try:
        for root in roots:
            entries = {}
            for path in [root, *sorted(root.rglob("*"))]:
                before = path.lstat()
                row = {"mode": stat.S_IMODE(before.st_mode)}
                if stat.S_ISLNK(before.st_mode):
                    target = path.resolve(strict=True)
                    if not any(target.is_relative_to(base) for base in roots):
                        raise RuntimeDrift("runtime link points outside inventory roots")
                    row.update(kind="symlink", link=os.readlink(path), target=str(target))
                elif stat.S_ISDIR(before.st_mode):
                    row.update(kind="directory")
                elif stat.S_ISREG(before.st_mode):
                    row.update(kind="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                else:
                    raise RuntimeDrift("runtime contains a special file")
                after = path.lstat()
                stable = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(before, name) != getattr(after, name) for name in stable):
                    raise RuntimeDrift("runtime entry changed during inventory")
                entries[path.relative_to(root).as_posix()] = row
            inventory[str(root)] = entries
    except (OSError, RuntimeError) as exc:
        raise RuntimeDrift(f"runtime inventory failed: {exc}") from exc
    record = {"schema_version": "aisle.matched-runtime.v1", "trees": inventory}
    record["immutable_id"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record


def verify_runtime(record):
    """Recompute the complete inventory; matching package names alone are insufficient."""
    if type(record) is not dict or type(record.get("trees")) is not dict:
        raise RuntimeDrift("runtime receipt is malformed")
    if capture_runtime(record["trees"]) != record:
        raise RuntimeDrift("runtime inventory has drifted")
