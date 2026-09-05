"""SPEC 440 live-graph check: the monolithic T1 graph runs one seeded
episode end to end through the broker (MON-5 guard route, MON-3 module
loading) and closes with the same verdict the typed graph reaches."""

from __future__ import annotations

import importlib.util
import json
import shutil

import pytest
from test_expert_graph import _run_expert_graph

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="needs the sim extra and the dora CLI",
    ),
]


def test_monolithic_t1_episode_succeeds(tmp_path):
    """MON-5, MON-3: graphs/monolithic_t1.yaml verbatim — the broker loads
    experts/monolithic/expert_t1.py, its commands traverse budget-guard,
    and the seed-3 ibuprofen episode that passes on expert_t1.yaml closes
    with status=success."""
    results, stderr = _run_expert_graph(
        tmp_path,
        "monolithic_t1.yaml",
        {"AISLE_SEEDS": "3", "AISLE_TARGET_MEDS": "ibuprofen", "AISLE_TIMEOUT_S": "60"},
    )

    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    assert records[0]["status"] == "success", (records[0], (stderr or "")[-2000:])
    assert (tmp_path / "monolith.json").is_file()  # the broker's module record
