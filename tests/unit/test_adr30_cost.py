"""ADR-30 fixed-horizon cost graph construction."""

import runpy
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
measurement_graph = runpy.run_path(str(ROOT / "tools/adr30_cost.py"))["measurement_graph"]


def test_free_run_cost_graph_is_explicitly_non_lockstep(tmp_path):
    """BRG-1/ADR-30 Cost: comparison mode removes every lockstep endpoint."""
    out = measurement_graph(ROOT / "graphs/expert_t0.yaml", tmp_path, "free-run", 60.0)
    doc = yaml.safe_load(out.read_text())
    ids = {node["id"] for node in doc["nodes"]}
    assert "turn-barrier" not in ids
    bridge = next(node for node in doc["nodes"] if node["id"] == "dora-genesis")
    assert "turn_commit" not in bridge["inputs"]
    assert "sim_turn" not in bridge["outputs"]
    for node in doc["nodes"]:
        assert "turn" not in node.get("inputs", {})
        assert "turn_done" not in node.get("outputs", [])
        assert not set(node.get("env", {})) & {
            "AISLE_LOCKSTEP",
            "AISLE_TURN_NODE",
            "AISLE_TURN_OUTPUTS",
            "AISLE_TURN_WALL_OUTPUTS",
        }


def test_lockstep_cost_graph_preserves_barrier_and_absolutizes_plan(tmp_path):
    """BRG-1/ADR-30 Cost: measured mode retains the attested topology."""
    out = measurement_graph(ROOT / "graphs/expert_s1.yaml", tmp_path, "lockstep", 60.0)
    doc = yaml.safe_load(out.read_text())
    barrier = next(node for node in doc["nodes"] if node["id"] == "turn-barrier")
    assert Path(barrier["env"]["AISLE_TURN_PLAN"]).is_absolute()
