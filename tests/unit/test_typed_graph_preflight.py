"""MON-6/MON-13: every staged worker must pass admission before graph transport starts."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_treatment_confinement import _attestation
from test_typed_graph_stage import _declarations, _validated
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _stage(tmp_path, *, share_home=False):
    from aisle.harness.treatment_ambient import build_declared_environment
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile
    from aisle.harness.typed_graph_stage import stage_typed_graph

    inputs = _validated(tmp_path)
    declarations = _declarations(inputs)
    shared = None
    for node_id, launch in declarations.items():
        if share_home and shared is not None:
            environment, environment_record = shared
        else:
            environment, environment_record = build_declared_environment(
                tmp_path / ("home-" + node_id),
                source_env={"PATH": "/usr/bin:/bin"},
            )
            shared = (environment, environment_record)
        policy = MacOSPolicy(
            **{
                key: value if key == "network_policy" else tuple(Path(p) for p in value)
                for key, value in launch["policy"].items()
            }
        )
        policy = replace(
            policy,
            visible_roots=(Path(launch["bundle"]),),
            output_roots=(Path(environment_record["home"]),),
            hidden_roots=tuple(
                dict.fromkeys((*policy.hidden_roots, *(Path(p) for p in launch["source_roots"])))
            ),
        )
        profile = tmp_path / "private" / (node_id + ".sb")
        compiled = compile_macos_profile(policy)
        profile.write_text(compiled.text)
        launch.update(
            policy=asdict(policy),
            environment=environment,
            environment_record=environment_record,
            profile_path=str(profile),
            attestation=_attestation(compiled, profile, tmp_path / "synthetic-adapter"),
        )
    declarations = json.loads(json.dumps(declarations, default=str))
    output = tmp_path / "private/staged"
    record = stage_typed_graph(
        ROOT, inputs["snapshot"], inputs["snapshot_record"], inputs["output"], declarations, output
    )
    return output, record


def test_whole_graph_preflight_checks_all_hosts_without_transport(tmp_path, monkeypatch):
    """MON-6/MON-13: all real worker launch declarations are checked without starting Dora."""
    from aisle.harness import typed_node_host
    from aisle.harness.typed_graph_stage import preflight_graph_stage

    output, record = _stage(tmp_path)
    monkeypatch.setattr(typed_node_host, "Node", lambda *a, **kw: pytest.fail("transport created"))
    result = preflight_graph_stage(output, record)
    assert set(result["hosts"]) == set(record["hosts"])
    assert result["stage_id"] == record["immutable_id"]
    assert not (output / "workers").exists()


@pytest.mark.parametrize("mutation", ["profile", "shared_home"])
def test_whole_graph_refuses_invalid_or_overlapping_workers(tmp_path, mutation):
    """MON-6: one invalid worker or shared mutable state invalidates the entire graph launch."""
    from aisle.harness.typed_graph_stage import StageError, preflight_graph_stage

    output, record = _stage(tmp_path, share_home=mutation == "shared_home")
    if mutation == "profile":
        config = json.loads(Path(next(iter(record["hosts"].values()))["config_path"]).read_text())
        Path(config["launch"]["profile_path"]).write_text("(allow default)")
    expected = "profile" if mutation == "profile" else "private state overlaps"
    with pytest.raises(StageError, match=expected):
        preflight_graph_stage(output, record)
    assert not (output / "workers").exists()


def test_validated_stage_can_start_all_unchanged_nodes(tmp_path):
    """MON-2/MON-12: validation, staging and preflight connect to real baseline worker children."""
    from test_turn_node import Raw

    from aisle.harness.typed_graph_stage import preflight_graph_stage
    from aisle.harness.typed_node_host import run_configured_node

    output, record = _stage(tmp_path)
    preflight_graph_stage(output, record)
    for binding in record["hosts"].values():
        result = run_configured_node(
            binding["config_path"],
            binding["config_sha256"],
            raw_node_factory=lambda: Raw([]),
        )
        assert result["ok"], result
        assert result["worker"]["input_exhausted"]
    assert len(list((output / "workers").glob("*/host.json"))) == 4
