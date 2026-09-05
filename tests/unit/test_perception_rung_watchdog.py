"""Graphs that run OWLv2 inside the turn declare a widened turn watchdog
(BRG-1; issue #391).

The perception rung (`l2_pose.py`, `label_reader.py`) infers on the CPU by
ratified design (D2, ADR-realistic-verifier), measured at ~1.6 s per OWLv2
call on a Linux x86-64 host. Under the 10 s default the ADR-30 barrier
expired before the first T2 turn closed, on CPU and CUDA alike — a host
assumption hidden inside a graph, and the shape of #268: wall time
selecting the outcome with every seed identical. `eval_t2_stack.yaml`
already declares 60/90 for this reason; every graph carrying the rung
must, so a slower host changes wall time, never pass/fail.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
GRAPHS = sorted((ROOT / "graphs").glob("*.yaml"))

#: Nodes that call OWLv2 inside the turn (CPU-pinned by D2).
PERCEPTION_RUNG = ("l2_pose.py", "label_reader.py")
#: The floor eval_t2_stack.yaml established for the rung (PR #304).
ORDINARY_FLOOR_S = 60.0
VERDICT_FLOOR_S = 90.0


def _nodes(path: Path) -> list[dict]:
    return (yaml.safe_load(path.read_text()) or {}).get("nodes", [])


def _barrier_env(nodes: list[dict]) -> dict:
    barriers = [n for n in nodes if str(n.get("path", "")).endswith("turn_barrier.py")]
    assert len(barriers) == 1, [n.get("id") for n in barriers]
    return barriers[0].get("env") or {}


def graphs_with_perception_rung():
    for path in GRAPHS:
        nodes = _nodes(path)
        if any(str(n.get("path", "")).endswith(PERCEPTION_RUNG) for n in nodes):
            yield path


def test_the_corpus_is_not_empty():
    """Guards the parametrised test from passing on zero graphs: the three
    graphs carrying the rung today are enumerated, so a renamed node path
    fails here rather than silently dropping out."""
    assert [p.name for p in graphs_with_perception_rung()] == [
        "eval_t2_stack.yaml",
        "expert_t1_l2.yaml",
        "expert_t2.yaml",
    ]


@pytest.mark.parametrize("path", list(graphs_with_perception_rung()), ids=lambda p: p.name)
def test_perception_rung_graph_widens_the_turn_watchdog(path: Path):
    """BRG-1 / #391: a graph whose turn contains CPU OWLv2 inference declares
    the widened barrier budget explicitly, for both turn types."""
    env = _barrier_env(_nodes(path))
    ordinary = float(env["AISLE_TURN_WATCHDOG_S"])
    verdict = float(env["AISLE_VERDICT_TURN_WATCHDOG_S"])
    assert ordinary >= ORDINARY_FLOOR_S, (path.name, ordinary)
    assert verdict >= VERDICT_FLOOR_S, (path.name, verdict)
    assert verdict > ordinary, (path.name, ordinary, verdict)
