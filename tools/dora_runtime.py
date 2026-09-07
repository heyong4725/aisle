#!/usr/bin/env python3
"""Install and verify AISLE's source-pinned Dora CLI (CON-3/CON-5/CON-8)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = "aisle-dora-receipt.json"
REPOSITORY = "https://github.com/dora-rs/dora.git"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_pin(path: Path) -> dict:
    pin = json.loads(path.read_text())
    if not isinstance(pin, dict) or pin.get("schema_version") != 1:
        raise ValueError("unsupported runtime pin schema")
    if pin.get("repository") != REPOSITORY:
        raise ValueError("runtime pin must name the official Dora repository")
    for key, pattern in (
        ("commit", r"[0-9a-f]{40}"),
        ("cargo_lock_sha256", r"[0-9a-f]{64}"),
        ("rust_toolchain", r"[0-9]+\.[0-9]+\.[0-9]+"),
        ("cli_version", r"[0-9]+\.[0-9]+\.[0-9]+"),
        ("python_api_version", r"[0-9]+\.[0-9]+\.[0-9]+"),
    ):
        if not isinstance(pin.get(key), str) or not re.fullmatch(pattern, pin[key]):
            raise ValueError(f"invalid immutable runtime pin: {key}")
    if pin.get("build_profile") != "dev":
        raise ValueError("unsupported runtime build profile")
    if pin.get("upstream_status") not in {"candidate", "validated"}:
        raise ValueError("runtime pin needs an explicit upstream status")
    return pin


def verify(pin_path: Path, prefix: Path, *, api_version: str | None = None) -> dict:
    pin = load_pin(pin_path)
    receipt = json.loads((prefix / RECEIPT).read_text())
    binary = prefix / "bin" / "dora"
    expected = {
        "schema_version": 1,
        "pin_sha256": sha(pin_path),
        "commit": pin["commit"],
        "cargo_lock_sha256": pin["cargo_lock_sha256"],
        "python_api_version": pin["python_api_version"],
        "binary_sha256": sha(binary),
    }
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("runtime receipt, source pin, or binary has drifted")
    if not os.access(binary, os.X_OK):
        raise ValueError("runtime binary is not executable")
    actual_api = api_version if api_version is not None else importlib.metadata.version("dora-rs")
    if actual_api != pin["python_api_version"]:
        raise ValueError("paired Dora Python API version mismatch")
    return {
        "ok": True,
        "scope": "installation_identity",
        "acceptance_ready": pin["upstream_status"] == "validated",
        "upstream_status": pin["upstream_status"],
        "binary": str(binary.resolve()),
        **expected,
    }


def _run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, check=True)
    # Child stderr goes to stderr; only the final CON-8 report uses stdout.
    return result.stdout.strip()


def install(pin_path: Path, prefix: Path) -> dict:
    pin = load_pin(pin_path)
    if prefix.exists():
        raise ValueError("installation prefix already exists; choose a fresh prefix")
    # Verify the paired API before downloading/building anything.
    api_version = importlib.metadata.version("dora-rs")
    if api_version != pin["python_api_version"]:
        raise ValueError("install the pinned sim extra before building the CLI")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aisle-dora-source-") as directory:
        source = Path(directory)
        _run(["git", "init", "-q"], source)
        _run(["git", "remote", "add", "origin", pin["repository"]], source)
        _run(["git", "fetch", "--depth", "1", "origin", pin["commit"]], source)
        _run(["git", "checkout", "--detach", "FETCH_HEAD"], source)
        if _run(["git", "rev-parse", "HEAD"], source) != pin["commit"]:
            raise ValueError("fetched Dora revision mismatch")
        if sha(source / "Cargo.lock") != pin["cargo_lock_sha256"]:
            raise ValueError("fetched Dora Cargo.lock mismatch")
        toolchain = pin["rust_toolchain"]
        rust_version = _run(["rustc", f"+{toolchain}", "--version"], source)
        if not rust_version.startswith(f"rustc {toolchain} "):
            raise ValueError("Rust toolchain identity mismatch")
        _run(
            [
                "cargo",
                f"+{toolchain}",
                "install",
                "--path",
                "binaries/cli",
                "--locked",
                "--debug",
                "--root",
                str(prefix.resolve()),
            ],
            source,
        )
        if sha(source / "Cargo.lock") != pin["cargo_lock_sha256"]:
            raise ValueError("build changed Dora Cargo.lock")
        binary = prefix / "bin" / "dora"
        version = _run([str(binary.resolve()), "--version"], source)
        if not re.search(rf"(?<![0-9.]){re.escape(pin['cli_version'])}(?![0-9.])", version):
            raise ValueError("built Dora CLI version mismatch")
        receipt = {
            "schema_version": 1,
            "pin_sha256": sha(pin_path),
            "commit": pin["commit"],
            "cargo_lock_sha256": pin["cargo_lock_sha256"],
            "python_api_version": pin["python_api_version"],
            "binary_sha256": sha(binary),
            "cli_version_output": version,
            "rust_version_output": rust_version,
            "platform": platform.platform(),
            "build_profile": pin["build_profile"],
        }
        with (prefix / RECEIPT).open("x") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
    return verify(pin_path, prefix, api_version=api_version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "verify"])
    parser.add_argument("--pin", type=Path, default=ROOT / "dora-runtime.json")
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = (install if args.command == "install" else verify)(
            args.pin.resolve(), args.prefix.resolve()
        )
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        report = {"ok": False, "error": str(exc)}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
