#!/usr/bin/env python3
"""env_hash: fingerprint the CON-7 frozen set (CON-5, CON-8).

Hashes src/aisle/{scenes,verifier,reset}, graphs/expert_*.yaml, and the
SPEC 080 frozen safety artifacts (env/limits.toml + the budget-guard
module) — sorted relative paths + file contents; __pycache__ excluded —
into one sha256. Modes: compute (default), --write (commit tools/env_hash.json),
--check (compare against the committed hash; rollout refuses on mismatch,
HAR-2). JSON on stdout, logs on stderr, exit 0 iff ok.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

FROZEN_DIRS = ("src/aisle/scenes", "src/aisle/verifier", "src/aisle/reset", "env")
# SPEC 080: the guard and its limits are frozen safety artifacts — a run's
# env_hash must change if either does
FROZEN_FILES = ("src/aisle/nodes/budget_guard.py",)
HASH_FILE = "tools/env_hash.json"


def frozen_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for d in FROZEN_DIRS:
        base = root / d
        if base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    files.extend(p for p in (root / "graphs").glob("expert_*.yaml") if p.is_file())
    files.extend(root / f for f in FROZEN_FILES if (root / f).is_file())
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def compute_env_hash(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = frozen_files(root)
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        # fixed-length per-file digest: file boundaries stay unambiguous
        # even when contents contain NUL bytes
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest(), len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help=f"write hash to {HASH_FILE}")
    mode.add_argument("--check", action="store_true", help=f"compare against {HASH_FILE}")
    args = parser.parse_args()

    env_hash, n_files = compute_env_hash(args.root)
    report: dict = {"ok": True, "env_hash": env_hash, "n_files": n_files}

    if args.write:
        hash_path = args.root / HASH_FILE
        hash_path.write_text(json.dumps({"env_hash": env_hash, "n_files": n_files}) + "\n")
        print(f"wrote {hash_path}", file=sys.stderr)
    elif args.check:
        hash_path = args.root / HASH_FILE
        committed = None
        if not hash_path.exists():
            error = f"{HASH_FILE} not found"
        else:
            try:
                committed = json.loads(hash_path.read_text())["env_hash"]
            except (json.JSONDecodeError, KeyError, TypeError):
                error = f"{HASH_FILE} is corrupted (expected JSON with an env_hash key)"
            else:
                error = None if committed == env_hash else "frozen set changed (CON-7)"
        if error:
            report = {"ok": False, "env_hash": env_hash, "committed": committed, "error": error}

    print(json.dumps(report))
    if not report["ok"]:
        print(f"env_hash check failed: {report['error']}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
