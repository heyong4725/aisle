"""Content receipts for controller-declared language runtime trees.

These receipts bind files, not OS authority or package suitability. Callers must
also verify that launch read grants and interpreter paths use the bound trees.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path


class RuntimeDrift(ValueError):
    """A runtime tree cannot be reconciled with its retained inventory."""


def worker_interpreter():
    """Select this runtime's direct executable without granting a re-exec launcher.

    Callers must still bind its bytes and verify membership in admitted runtime
    trees. Selection never adds a read root or an executable grant by itself.
    """
    framework = sysconfig.get_config_var("PYTHONFRAMEWORK")
    if framework:
        python = (
            Path(sys.base_prefix)
            / "Resources"
            / (framework + ".app")
            / "Contents/MacOS"
            / framework
        )
    else:
        python = Path(sys.executable)
    python = python.resolve(strict=True)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeDrift("direct worker interpreter is not executable")
    return python


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

    def entry(path):
        before = path.lstat()
        row = {"mode": stat.S_IMODE(before.st_mode)}
        if stat.S_ISLNK(before.st_mode):
            # Bind internal dangling links too: later creation or retargeting
            # changes the inventory, even for optional framework headers.
            target = path.resolve(strict=False)
            if not any(target.is_relative_to(base) for base in roots):
                raise RuntimeDrift("runtime link points outside inventory roots")
            row.update(kind="symlink", link=os.readlink(path), target=str(target))
        elif stat.S_ISDIR(before.st_mode):
            row.update(kind="directory")
        elif stat.S_ISREG(before.st_mode):
            with path.open("rb") as stream:
                row.update(kind="file", sha256=hashlib.file_digest(stream, "sha256").hexdigest())
        else:
            raise RuntimeDrift("runtime contains a special file")
        after = path.lstat()
        stable = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in stable):
            raise RuntimeDrift("runtime entry changed during inventory")
        return row

    inventory = {}
    try:
        # Opening thousands of package files dominates verification on the
        # development host. Overlap only four reads, with bounded buffers and
        # queued work; retain ordered results and every per-entry drift check.
        # The context joins all readers, including on failure, before return.
        with ThreadPoolExecutor(max_workers=4) as readers:
            for root in roots:
                entries = {}
                paths = iter([root, *sorted(root.rglob("*"))])
                while batch := list(islice(paths, 64)):
                    for path, row in zip(batch, readers.map(entry, batch), strict=True):
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
