"""Shared helpers for unit tests that drive the repo CLIs as subprocesses."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cli(argv: list[str]) -> subprocess.CompletedProcess:
    """Run the test venv's python with argv; capture text output."""
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True)


def run_tool(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run tools/<script> with args."""
    return run_cli([str(REPO_ROOT / "tools" / script), *args])


def run_module(module: str, *args: str) -> subprocess.CompletedProcess:
    """Run a package CLI via python -m."""
    return run_cli(["-m", module, *args])


def run_json(module: str, *args: str) -> tuple[int, dict]:
    """Run a package CLI; return (exit code, parsed JSON stdout)."""
    proc = run_module(module, *args)
    return proc.returncode, json.loads(proc.stdout)


def make_registry_root(tmp_path: Path) -> Path:
    """Repo-shaped root with the real schema files and an empty manifests dir."""
    schema_dir = tmp_path / "registry" / "schema"
    schema_dir.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "registry" / "schema" / "capability.schema.json", schema_dir)
    shutil.copy(REPO_ROOT / "registry" / "schema" / "schemas.toml", schema_dir)
    (tmp_path / "registry" / "manifests").mkdir()
    return tmp_path


def write_manifest(root: Path, manifest: dict) -> None:
    path = root / "registry" / "manifests" / f"{manifest['id']}.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
