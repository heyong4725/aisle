from pathlib import Path

import pytest

from aisle.harness.monolithic import (
    TypedSurfaceError,
    classify_bypass_attempt,
    validate_broker_route,
    validate_interface_map,
    validate_matched_treatment,
    validate_monolithic_surface,
    validate_trusted_preflight,
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


def test_monolithic_surface_rejects_typed_facilities():
    with pytest.raises(TypedSurfaceError, match="forbidden"):
        validate_monolithic_surface(["control.py", "capability_registry.py"])


def test_interface_map_rejects_semantic_mismatch():
    with pytest.raises(TypedSurfaceError, match="mismatch"):
        validate_interface_map(
            [{"name": "action", "typed": "cmd", "monolithic": "other", "authority": "task"}]
        )


def test_broker_route_rejects_direct_actuation():
    with pytest.raises(TypedSurfaceError, match="guard"):
        validate_broker_route(["trusted_controller", "primitive_broker", "driver"])


def test_trusted_preflight_fails_closed_when_unresolved():
    with pytest.raises(TypedSurfaceError, match="unresolved"):
        validate_trusted_preflight(
            {"hashes": {}, "confinement": False, "route_map": {}, "evidence_sink": ""}
        )


def test_bypass_attempts_classify_and_unknown_fails_closed():
    assert classify_bypass_attempt("read hidden seed") == "hidden_seed_access"
    with pytest.raises(TypedSurfaceError, match="unclassified"):
        classify_bypass_attempt("invent a new bypass")


def test_matched_treatment_rejects_undeclared_difference():
    with pytest.raises(TypedSurfaceError, match="undeclared"):
        validate_matched_treatment(
            {"budget": 1, "arm": "typed"}, {"budget": 2, "arm": "mono"}, {"arm"}
        )
