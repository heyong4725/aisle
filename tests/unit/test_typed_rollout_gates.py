"""MON-2/MON-13/HAR-2: retained typed validation augments the ordinary rollout gates."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_typed_graph_preflight import _stage
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _gate_inputs(tmp_path, monkeypatch, *, env_ok=True, episodes_left=10, attested=True):
    from aisle.harness import rollout

    stage, record = _stage(tmp_path)
    graph = Path(record["snapshot_record"]["snapshot_root"]) / "graphs/expert_t1.yaml"
    monkeypatch.setattr(
        rollout, "resolve_sim_identity", lambda _: {"ok": True, "sim_backend": "genesis"}
    )
    monkeypatch.setattr(rollout, "resolve_trusted_baseline", lambda *a: ("1" * 40, None))
    monkeypatch.setattr(
        rollout, "budget_remaining", lambda _: {"episodes_left": episodes_left, "wall_h_left": 1}
    )
    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0 if env_ok else 1,
            stdout=json.dumps({"env_hash": "fixture", "dist": {"attested": attested}}),
        ),
    )
    return dict(
        root=ROOT,
        graph=graph,
        branch="synthetic-typed-gate",
        no_idea_gate=True,
        env_baseline="origin/main",
        episodes=1,
        graph_snapshot=graph.read_bytes(),
        typed_stage=(stage, record),
    )


def test_bound_validation_uses_authored_registry_and_retains_gate_identity(tmp_path, monkeypatch):
    """MON-2/HAR-2: the real confined validator's result feeds the trusted gate pipeline."""
    from aisle.harness import rollout

    kwargs = _gate_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        rollout, "validate", lambda *a, **kw: pytest.fail("validator rerun on controller")
    )
    result = rollout.run_gates(**kwargs)
    assert result["ok"], result
    assert result["typed_stage_id"] == kwargs["typed_stage"][1]["immutable_id"]
    assert result["env_hash"] == "fixture"
    assert result["env_attested"]


@pytest.mark.parametrize(
    "failure", ["environment", "distribution", "budget", "idea", "lockstep", "embodiment", "bytes"]
)
def test_staged_validation_cannot_bypass_other_gates(tmp_path, monkeypatch, failure):
    """HAR-2/MON-13: staged validation cannot replace environment, budget, idea or input checks."""
    from aisle.harness import rollout

    kwargs = _gate_inputs(
        tmp_path,
        monkeypatch,
        env_ok=failure != "environment",
        attested=failure != "distribution",
        episodes_left=0 if failure == "budget" else 10,
    )
    if failure == "idea":
        kwargs["no_idea_gate"] = False
        monkeypatch.setattr(rollout, "open_ideas", lambda *a: [])
    elif failure == "lockstep":
        from aisle.harness import validate

        monkeypatch.setattr(
            validate, "_clock_topology", lambda *a: {"bridge_ids": ["bridge"], "barrier_ids": []}
        )
    elif failure == "embodiment":
        kwargs["embodiment"] = "so101"
    elif failure == "bytes":
        kwargs["graph_snapshot"] += b"\n"
    result = rollout.run_gates(**kwargs)
    assert not result["ok"]
    assert result["gate"] == {
        "environment": "env_hash",
        "distribution": "dist",
        "budget": "budget",
        "idea": "idea",
        "lockstep": "lockstep",
    }.get(failure, "typed_stage")
