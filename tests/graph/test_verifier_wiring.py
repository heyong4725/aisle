"""SPEC 040 VER-4: oracle_state isolation, enforced by the validator
(VAL-6). Marker graph (CON-12): validates graph wiring via the harness
CLI; no dataflow launch needed for this property."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.graph

REPO_ROOT = Path(__file__).resolve().parents[2]


def validate(nodes, tmp_path):
    graph = tmp_path / "g.yaml"
    graph.write_text(yaml.safe_dump({"nodes": nodes}, sort_keys=False))
    proc = subprocess.run(
        [sys.executable, "-m", "aisle.harness.cli", "validate", str(graph)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_oracle_only_edge(tmp_path):
    """VER-4: the verifier is the ONLY permitted consumer of oracle_state —
    the validator accepts the verifier edge and rejects any other consumer
    with ORACLE_LEAK."""
    code, report = validate(
        [
            {"id": "camera-source", "outputs": ["oracle_state"]},
            {
                "id": "verifier-oracle",
                "inputs": {"oracle_state": "camera-source/oracle_state"},
                "outputs": ["episode_result"],
            },
        ],
        tmp_path,
    )
    assert code == 0 and report["ok"], report

    code, report = validate(
        [
            {"id": "camera-source", "outputs": ["oracle_state"]},
            {
                "id": "task-state-machine",
                "inputs": {"episode_result": "camera-source/oracle_state"},
                "outputs": ["target_request"],
            },
        ],
        tmp_path,
    )
    assert code != 0
    assert any(e["code"] == "ORACLE_LEAK" for e in report["errors"]), report
