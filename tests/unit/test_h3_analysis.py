"""Unit tests for tools/h3_analysis.py (design doc §11.5 transfer curve;
ADR-h3-campaign-protocol §7 verdict). Pure record assembly — no sim."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from h3_analysis import cell, h3_verdict, load_scenarios, results_markdown  # noqa: E402

pytestmark = pytest.mark.unit


def record(arm, tier, first_success=100.0, prior=(), holdout_ok=True, pass1=0.5):
    return {
        "arm": arm,
        "tier": tier,
        "budgets": {"tier": tier, "tokens": 1000, "episodes": 8, "wall_h": 1.0},
        "session": {"stopped": "agent_done", "rc": 0, "tokens": 900, "wall_s": 1800.0},
        "frozen_drift": [],
        "holdout": {
            "ok": holdout_ok,
            "error": None if holdout_ok else "scoring window expired",
            "pass1": pass1,
            "pass8": pass1,
            "failures": {},
        },
        "first_success_wall_s": first_success,
        "wrong_object_total": 0,
        "rollouts": [],
        "prior_skills": list(prior),
        "skills_after": list(prior),
        "skill_reuse_in_deliverable": [],
    }


def test_cell_flags_wipe_leak_and_partial_holdout():
    """The wiped arm (W) must start every scenario with an empty library:
    a non-empty prior_skills on a W record is direct evidence of the
    campaign-2 wipe leak and must surface as a flag, not vanish into the
    table. A holdout with ok=false is a partial cell, never a clean 0."""
    leaked = cell(record("W", "S2", prior=["s1-driver-v2"], holdout_ok=False))
    assert "wipe_leak" in leaked["flags"] and "holdout_partial" in leaked["flags"]
    clean_w = cell(record("W", "S1"))
    assert clean_w["flags"] == []
    library_arm = cell(record("L", "S2", prior=["s1-driver-v2"]))
    assert "wipe_leak" not in library_arm["flags"]  # L is SUPPOSED to carry skills


def test_verdict_ratio_semantics():
    """ADR §7: met iff arm L's S2 AND S3 time-to-first-success <= 0.5x
    arm W's, per tier. A tier where L never succeeded is not-met; a tier
    where only W never succeeded is met (L transferred, W could not)."""
    cells = [
        cell(r)
        for r in (
            record("W", "S1", first_success=1000),
            record("W", "S2", first_success=1000),
            record("W", "S3", first_success=1000),
            record("L", "S1", first_success=900),
            record("L", "S2", first_success=400),
            record("L", "S3", first_success=600),
        )
    ]
    verdict = h3_verdict(cells)
    assert verdict["ratios"]["S2"] == pytest.approx(0.4)
    assert verdict["per_tier"] == {"S2": True, "S3": False}  # 0.6 > 0.5
    assert verdict["met"] is False

    fast = [c if c["arm"] == "W" else {**c, "first_success_wall_s": 300.0} for c in cells]
    assert h3_verdict(fast)["met"] is True

    no_l = [{**c, "first_success_wall_s": None} if c["arm"] == "L" else c for c in cells]
    assert h3_verdict(no_l)["per_tier"] == {"S2": False, "S3": False}
    no_w = [{**c, "first_success_wall_s": None} if c["arm"] == "W" else c for c in cells]
    assert h3_verdict(no_w)["per_tier"] == {"S2": True, "S3": True}


def test_verdict_incomplete_campaign_is_none_with_caveats():
    """Half a campaign never yields a verdict — met stays None until both
    arms' S2/S3 cells exist; wipe-leak flags surface as verdict caveats
    with the bias direction stated."""
    cells = [
        cell(record("W", "S1")),
        cell(record("W", "S2", prior=["s1-driver-v2"])),
        cell(record("W", "S3", prior=["s1-driver-v2"])),
    ]
    verdict = h3_verdict(cells)
    assert verdict["met"] is None
    assert any("wipe_leak" in c for c in verdict["caveats"])


def test_load_and_cli_shape(tmp_path):
    """CON-8: single JSON object on stdout, exit 0; --markdown writes the
    table with partial cells marked."""
    for arm, tier in (("W", "S1"), ("L", "S1")):
        d = tmp_path / f"arm_{arm}" / tier
        d.mkdir(parents=True)
        r = record(arm, tier, holdout_ok=(arm == "L"))
        (d / "scenario.json").write_text(json.dumps(r))
    assert len(load_scenarios(tmp_path)) == 2

    md = tmp_path / "table.md"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "h3_analysis.py"),
            "--dir",
            str(tmp_path),
            "--markdown",
            str(md),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["ok"] is True and len(out["cells"]) == 2
    assert out["verdict"]["met"] is None  # S2/S3 cells absent
    text = md.read_text()
    assert "partial" in text  # the W cell's expired holdout is marked
    assert "| W " in text and "| L " in text


def test_markdown_marks_leak_and_stop_reason():
    cells = [
        cell(record("W", "S2", prior=["s1-driver-v2"], holdout_ok=False)),
        cell(record("L", "S2")),
    ]
    text = results_markdown(cells, h3_verdict(cells))
    assert "wipe_leak" in text and "agent_done" in text
