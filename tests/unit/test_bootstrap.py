"""Unit tests for the T01 repository bootstrap (CON-6, CON-12, CON-1, CON-2, CON-9)."""

import tomllib

import pytest
from cli_helpers import REPO_ROOT

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
    """CON-1: CUDA-only dependencies MUST NOT enter the DEFAULT dependency
    set — checked over the resolved lock closure reachable from the
    declared default dependencies, so a transitive CUDA pull-in fails.
    CON-1 explicitly permits CUDA behind an optional extra: `cuda` is that
    extra and the only path by which a CUDA wheel may enter the lock. The
    `sim` extra resolves the CPU torch on Linux and stays clean."""
    forbidden = ("cuda", "nvidia", "cu11", "cu12")
    project = load_pyproject()["project"]
    for dep in project.get("dependencies", []):
        assert not any(k in dep.lower() for k in forbidden), dep

    with open(REPO_ROOT / "uv.lock", "rb") as f:
        lock = tomllib.load(f)
    # merge duplicate [[package]] entries (multi-platform locks repeat names)
    graph: dict[str, list[str]] = {}
    for p in lock.get("package", []):
        graph.setdefault(p["name"], []).extend(d["name"] for d in p.get("dependencies", []))
    roots = [
        dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower()
        for dep in project.get("dependencies", [])
    ]
    missing_roots = [r for r in roots if r not in graph]
    assert not missing_roots, f"declared deps absent from lock graph: {missing_roots}"
    closure: set[str] = set()
    frontier = list(roots)
    while frontier:
        name = frontier.pop()
        if name in closure:
            continue
        closure.add(name)
        frontier.extend(graph.get(name, []))
    assert closure, "default dependency closure resolved to nothing — check lock parsing"
    for name in sorted(closure):
        assert not any(k in name.lower() for k in forbidden), name
    # CUDA/NVIDIA wheels are confined to the `cuda` extra, which CON-1
    # explicitly sanctions. The lock keys torch variants by version
    # (2.13.0+cpu vs 2.13.0+cu130), so the invariant is: the ONLY package
    # that may pull a forbidden wheel is a +cu torch. `sim` resolves the
    # +cpu variant on linux and therefore stays clean.
    cuda_parents = {
        (p["name"], p["version"])
        for p in lock.get("package", [])
        for d in p.get("dependencies", [])
        if any(k in d["name"].lower() for k in forbidden)
    }
    offenders = sorted(
        (name, version)
        for name, version in cuda_parents
        if not any(k in name.lower() for k in forbidden)
        and not (name == "torch" and "+cu" in version)
    )
    assert not offenders, f"CUDA wheels reachable outside the cuda extra: {offenders}"
    assert any(name == "torch" and "+cu" in version for name, version in cuda_parents), (
        "no CUDA torch in the lock — the cuda extra is inert"
    )


def test_cuda_extra_provisions_gpu_torch():
    """CON-1: CUDA-only dependencies MAY live behind an optional `cuda`
    extra. The extra MUST actually provision a GPU torch, otherwise
    `scenes.pharmacy.select_genesis_backend` can never resolve to cuda on
    Linux — the default CPU index would silently win."""
    pyproject = load_pyproject()
    extras = pyproject["project"].get("optional-dependencies", {})
    assert "cuda" in extras, "CON-1 names `cuda` as the sanctioned GPU extra"
    assert any("torch" in dep.lower() for dep in extras["cuda"]), extras["cuda"]

    # the extra is inert unless torch resolves from a CUDA index on linux
    indexes = {i["name"]: i["url"] for i in pyproject["tool"]["uv"].get("index", [])}
    cuda_indexes = [n for n, url in indexes.items() if "/cu" in url]
    assert cuda_indexes, f"no CUDA wheel index declared: {indexes}"


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
