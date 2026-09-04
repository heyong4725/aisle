from pathlib import Path

import pytest

from aisle.harness.monolithic import (
    TypedSurfaceError,
    validate_typed_graph,
    validate_typed_surface,
)

pytestmark = pytest.mark.unit


def test_typed_surface_rejects_unallowlisted_files(tmp_path: Path):
    with pytest.raises(TypedSurfaceError, match="allowlist"):
        validate_typed_surface(
            tmp_path, ["graphs/task.yaml", "extra.py"], ["graphs/task.yaml", "src/node.py"]
        )


def test_typed_surface_requires_existing_allowlisted_files(tmp_path: Path):
    with pytest.raises(TypedSurfaceError, match="missing"):
        validate_typed_surface(tmp_path, ["graphs/task.yaml"], ["graphs/task.yaml"])


def test_typed_graph_uses_pinned_validator_without_repair(tmp_path: Path):
    report = validate_typed_graph(tmp_path / "missing.yaml", tmp_path, "franka")
    assert report["ok"] is False
    assert report["errors"]
