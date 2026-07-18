"""Unit tests for the T01 repository bootstrap (CON-6, CON-12, CON-1, CON-2, CON-9)."""

import tomllib

import pytest
from conftest import REPO_ROOT

pytestmark = pytest.mark.unit


def load_pyproject() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_layout():
    """CON-6: the fixed repository layout exists."""
    required = [
        "specs",
        "src/aisle/scenes",
        "src/aisle/nodes",
        "src/aisle/verifier",
        "src/aisle/reset",
        "src/aisle/harness",
        "graphs",
        "registry/schema",
        "registry/manifests",
        "skills",
        "tests/unit",
        "tests/sim",
        "tests/graph",
        "tests/accept",
        "tools",
        "runs",
        "docs",
    ]
    missing = [d for d in required if not (REPO_ROOT / d).is_dir()]
    assert missing == []


def test_runs_gitignored():
    """CON-6: runs/ is gitignored."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert "runs/" in gitignore


def test_pytest_markers():
    """CON-12: pytest markers unit, sim, graph, accept are registered and
    strict (unknown markers are errors)."""
    cfg = load_pyproject()["tool"]["pytest"]["ini_options"]
    registered = {m.split(":")[0].strip() for m in cfg["markers"]}
    assert {"unit", "sim", "graph", "accept"} <= registered
    assert "--strict-markers" in cfg["addopts"]


def test_python_version_and_layout_cfg():
    """CON-2: Python 3.11+, one workspace pyproject, packages under src/aisle."""
    project = load_pyproject()["project"]
    assert project["requires-python"].startswith(">=3.11")
    assert (REPO_ROOT / "src" / "aisle" / "__init__.py").exists()


def test_no_cuda_in_default_dependencies():
    """CON-1: CUDA-only dependencies MUST NOT enter the default dependency set."""
    project = load_pyproject()["project"]
    forbidden = ("cuda", "nvidia", "cu11", "cu12")
    for dep in project.get("dependencies", []):
        assert not any(k in dep.lower() for k in forbidden), dep


def test_ci_script_gate_order():
    """CON-9: tools/ci.sh runs the local CI gates in the constitution's order:
    ruff format --check, ruff check, pytest -m unit, then trace_check."""
    lines = (REPO_ROOT / "tools" / "ci.sh").read_text().splitlines()
    script = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    order = [
        script.index("ruff format --check"),
        script.index("ruff check"),
        script.index("pytest -m unit"),
        script.index("trace_check.py"),
    ]
    assert order == sorted(order)
