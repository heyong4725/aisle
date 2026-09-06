"""SPEC 480 live-graph checks (issue #352): the semantic gateway inside a
running dataflow permits a correct plan end to end and refuses a
wrong-object closure the same policy makes under the adversary, with the
guard and verifier identical across arms (SEM-5, SEM-8)."""

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

ENV = {"AISLE_SEEDS": "3", "AISLE_TARGET_MEDS": "ibuprofen", "AISLE_TIMEOUT_S": "60"}


def _records(results, stderr):
    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    return records[0]


def test_oracle_shield_permits_the_correct_plan(tmp_path):
    """SEM-4 / SEM-5: with the ceiling identity adapter the seed-3 ibuprofen
    episode closes with status=success and every stage was permitted."""
    record = _records(*_run_expert_graph(tmp_path, "shield_t0_oracle.yaml", ENV))
    assert record["status"] == "success", record
    events = [
        json.loads(line) for line in (tmp_path / "shield_events.jsonl").read_text().splitlines()
    ]
    assert events and all(e["outcome"] == "permit" for e in events), events[:5]


def test_oracle_shield_refuses_the_wrong_object_closure(tmp_path):
    """SEM-4 / SEM-8: under goal-adversary the policy pursues the wrong box;
    the gateway refuses at pre_grasp and the verifier never records a
    wrong-object delivery (the disturbed box is a `collision`, a verifier
    semantic outcome, not a guard intervention)."""
    record = _records(*_run_expert_graph(tmp_path, "shield_t0_oracle_adversary.yaml", ENV))
    assert record["failure"] != "wrong_object", record
    events = [
        json.loads(line) for line in (tmp_path / "shield_events.jsonl").read_text().splitlines()
    ]
    refusals = [e for e in events if e["outcome"] == "refuse"]
    assert refusals and refusals[0]["stage"] == "pre_grasp", events[:5]
    assert refusals[0]["reason"] == "wrong_target"


def test_no_shield_arm_forwards_the_refused_closure(tmp_path):
    """SEM-10 control: the same adversary graph without enforcement forwards
    the closure the authorizer refused (logged, not enforced). The oracle
    verifier classifies disturbing the wrong box as `collision` before any
    delivery, in both arms, so the verdict alone does not separate the arms
    here; the shield_events record does (SEM-8 evidence)."""
    record = _records(*_run_expert_graph(tmp_path, "shield_t0_none_adversary.yaml", ENV))
    assert record["status"] != "success", record
    events = [
        json.loads(line) for line in (tmp_path / "shield_events.jsonl").read_text().splitlines()
    ]
    refused = [e for e in events if e["outcome"] == "refuse"]
    assert refused and all(e["forwarded"] for e in refused), events[:5]
