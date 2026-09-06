"""SPEC 460 THR-11: Claude and Codex campaign paths must run the identical
catalog under matched authority; until the #353 confinement adapter exists
neither path is attested, so the parity report records every attack as
not_executed and no fixture pass authorizes a claim about either."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT

from aisle.harness import attack_catalog as ac

pytestmark = pytest.mark.unit


def test_unresolved_paths_mismatch_on_every_dimension():
    """THR-11: an unattested authority profile matches nothing."""
    claude, codex = ac.agent_path_profile("claude"), ac.agent_path_profile("codex")
    assert claude["resolved"] is False and codex["resolved"] is False
    assert ac.matched_profiles(claude, codex) == list(ac.AUTHORITY_DIMENSIONS)
    fixture = ac.agent_path_profile("fixture")
    assert fixture["resolved"] is True and ac.matched_profiles(fixture, fixture) == []
    with pytest.raises(ValueError):
        ac.agent_path_profile("gemini")


def test_parity_report_records_not_executed_rather_than_borrowing_the_fixture():
    """THR-11 / THR-12: both paths carry every attack as not_executed with
    the reason, the report is not ok, and the wording licenses no claim."""
    report = ac.agent_path_parity(("claude", "codex"))
    assert report["ok"] is False
    assert report["paths"] == ["claude", "codex"]
    for path in report["paths"]:
        run = report["runs"][path]
        assert run["executed"] is False and "#353" in run["reason"]
        assert {a["outcome"] for a in run["attacks"]} == {"not_executed"}
        assert run["counts"]["not_executed"] == len(ac.ATTACKS)
    assert report["profile_mismatches"]["claude:codex"] == list(ac.AUTHORITY_DIMENSIONS)
    assert "authorizes no claim" in report["claim_wording"]


def test_committed_parity_record_matches_the_current_catalog():
    """THR-12: the retained record names the catalog version and the same
    not-executed outcome for both paths."""
    record = json.loads(
        (Path(REPO_ROOT) / "analysis/threat-model/agent-path-parity.json").read_text()
    )
    assert record["catalog_version"] == ac.CATALOG_VERSION
    assert record["ok"] is False
    assert all(not record["runs"][p]["executed"] for p in ("claude", "codex"))
