"""Unit tests for tools/h3_analysis.py (design doc §11.5, §8.4;
ADR-h3 §7 verdict). Pure record assembly — no sim."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from h3_analysis import (  # noqa: E402
    cell,
    h3_verdict,
    load_campaign,
    results_markdown,
)

pytestmark = pytest.mark.unit

TREATMENT = {
    "commit": "abc123",
    "agent": "claude",
    "model": "m",
    "dev_seeds": "0..49",
    "holdout_seeds": "100..107",
}


def record(
    arm,
    tier,
    first_success=100.0,
    prior=(),
    holdout_ok=True,
    pass1=0.5,
    drift=(),
    samples=(),
    failures=None,
):
    return {
        "arm": arm,
        "tier": tier,
        "budgets": {"tier": tier, "tokens": 1000, "episodes": 8, "wall_h": 1.0},
        "session": {"stopped": "agent_done", "rc": 0, "tokens": 900, "wall_s": 1800.0},
        "frozen_drift": list(drift),
        "holdout": {
            "ok": holdout_ok,
            "error": None if holdout_ok else "scoring window expired",
            "pass1": pass1,
            "pass8": pass1,
            "failures": dict(failures or {}),
        },
        "first_success_wall_s": first_success,
        "wrong_object_total": 0,
        "rollouts": [],
        "prior_skills": list(prior),
        "skills_after": list(prior),
        "skill_reuse_in_deliverable": [],
        "_token_samples": list(samples),
    }


def test_cell_flags_are_derived_from_the_record():
    """wipe_leak (prior skills on the WIPED arm), frozen_drift (non-empty
    audit), and holdout_partial (expired window) each flag the cell —
    PR #59 review: a drifted or partial cell must never read as clean."""
    leaked = cell(record("W", "S2", prior=["s1-driver-v2"], holdout_ok=False))
    assert "wipe_leak" in leaked["flags"] and "holdout_partial" in leaked["flags"]
    drifted = cell(record("L", "S2", drift=["src/aisle/verifier/oracle.py"]))
    assert drifted["flags"] == ["frozen_drift"]
    assert cell(record("W", "S1"))["flags"] == []
    library_arm = cell(record("L", "S2", prior=["s1-driver-v2"]))
    assert "wipe_leak" not in library_arm["flags"]  # L is SUPPOSED to carry skills


def test_tokens_to_first_success_from_samples():
    """Design doc §8.4: TOKENS-to-success is a first-class metric — read
    from the session's live token samples at the first-success wall time."""
    samples = [
        {"wall_s": 5.0, "tokens": 0},
        {"wall_s": 100.0, "tokens": 40_000},
        {"wall_s": 200.0, "tokens": 90_000},
    ]
    c = cell(record("W", "S1", first_success=150.0, samples=samples))
    assert c["tokens_to_first_success"] == 90_000  # first sample at/after 150 s
    none_first = cell(record("W", "S1", first_success=None, samples=samples))
    assert none_first["tokens_to_first_success"] is None
    late = cell(record("W", "S1", first_success=999.0, samples=samples))
    assert late["tokens_to_first_success"] == 90_000  # beyond samples: last known


def test_verdict_ratio_semantics():
    """ADR §7: met iff arm L's S2 AND S3 time-to-first-success <= 0.5x
    arm W's, per tier; token ratios reported alongside (§8.4). A tier
    where L never succeeded is not-met; where only W never succeeded,
    met (L transferred, W could not)."""
    w_samples = [{"wall_s": 0, "tokens": 0}, {"wall_s": 2000, "tokens": 800_000}]
    l_samples = [{"wall_s": 0, "tokens": 0}, {"wall_s": 2000, "tokens": 200_000}]
    cells = [
        cell(r)
        for r in (
            record("W", "S1", first_success=1000),
            record("W", "S2", first_success=1000, samples=w_samples),
            record("W", "S3", first_success=1000),
            record("L", "S1", first_success=900),
            record("L", "S2", first_success=400, samples=l_samples),
            record("L", "S3", first_success=600),
        )
    ]
    verdict = h3_verdict(cells)
    assert verdict["ratios"]["S2"] == pytest.approx(0.4)
    assert verdict["token_ratios"]["S2"] == pytest.approx(0.25)
    assert verdict["per_tier"] == {"S2": True, "S3": False}  # 0.6 > 0.5
    assert verdict["met"] is False

    fast = [c if c["arm"] == "W" else {**c, "first_success_wall_s": 300.0} for c in cells]
    assert h3_verdict(fast)["met"] is True

    no_l = [{**c, "first_success_wall_s": None} if c["arm"] == "L" else c for c in cells]
    assert h3_verdict(no_l)["per_tier"] == {"S2": False, "S3": False}
    no_w = [{**c, "first_success_wall_s": None} if c["arm"] == "W" else c for c in cells]
    assert h3_verdict(no_w)["per_tier"] == {"S2": True, "S3": True}


def test_verdict_uses_only_clean_cells():
    """PR #57/#59 reviews: wipe_leak, frozen_drift, and holdout_partial
    cells are ALL ineligible — flagged pairs leave the verdict pending
    (None); once a clean higher-attempt rerun exists the verdict uses IT
    while the flagged history stays in the caveats."""
    leaked_w2 = cell(record("W", "S2", first_success=100.0, prior=["s1-driver-v2"]))
    drifted_w3 = cell(record("W", "S3", first_success=100.0, drift=["env/x"]))
    partial_l2 = cell(record("L", "S2", first_success=400.0, holdout_ok=False))
    l3 = cell(record("L", "S3", first_success=400.0))
    assert h3_verdict([leaked_w2, drifted_w3, partial_l2, l3])["met"] is None

    rerun_w2 = cell({**record("W", "S2", first_success=1000.0), "attempt": 2})
    rerun_w3 = cell({**record("W", "S3", first_success=1000.0), "attempt": 2})
    clean_l2 = cell(record("L", "S2", first_success=400.0))
    verdict = h3_verdict([leaked_w2, drifted_w3, rerun_w2, rerun_w3, clean_l2, l3])
    assert verdict["ratios"]["S2"] == pytest.approx(0.4)  # rerun's 1000, not leaked 100
    assert verdict["met"] is True
    assert any("wipe_leak" in c for c in verdict["caveats"])
    assert any("frozen_drift" in c for c in verdict["caveats"])


def _write_campaign(tmp_path, treatment=TREATMENT, arms=(("W", "S1"), ("L", "S1"))):
    (tmp_path / "h3_results.json").write_text(
        json.dumps({"ok": True, "treatment": treatment, "records": []})
    )
    for arm, tier in arms:
        d = tmp_path / f"arm_{arm}" / tier
        d.mkdir(parents=True)
        (d / "scenario.json").write_text(json.dumps(record(arm, tier, holdout_ok=(arm == "L"))))
        (d / "token_samples.jsonl").write_text('{"wall_s": 5.0, "tokens": 1000}\n')


def test_load_and_cli_shape(tmp_path):
    """CON-8: single JSON object on stdout, exit 0; --markdown writes the
    table with partial cells marked and the treatment echoed."""
    _write_campaign(tmp_path)
    campaign = load_campaign(tmp_path)
    assert len(campaign["records"]) == 2 and campaign["treatment"]["commit"] == "abc123"

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
    assert out["treatment"]["commit"] == "abc123"
    assert out["verdict"]["met"] is None  # S2/S3 cells absent
    text = md.read_text()
    assert "partial" in text and "| W " in text and "| L " in text


def test_cli_fails_closed_on_missing_or_malformed_input(tmp_path):
    """PR #59 review (CON-8): an empty dir, a malformed scenario file,
    and a treatment mismatch must each refuse with a JSON error and exit
    1 — never an empty ok:true or a traceback."""

    def run(d):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "h3_analysis.py"), "--dir", str(d)],
            capture_output=True,
            text=True,
        )

    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run(empty)
    out = json.loads(proc.stdout)
    assert proc.returncode == 1 and out["ok"] is False and "h3_results" in out["error"]

    bad = tmp_path / "bad"
    bad.mkdir()
    _write_campaign(bad)
    (bad / "arm_W" / "S1" / "scenario.json").write_text("{not json")
    proc = run(bad)
    out = json.loads(proc.stdout)
    assert proc.returncode == 1 and out["ok"] is False and "malformed" in out["error"]

    mixed = tmp_path / "mixed"
    mixed.mkdir()
    _write_campaign(mixed)
    (mixed / "h3_results-r2.json").write_text(
        json.dumps({"ok": True, "treatment": {**TREATMENT, "commit": "OTHER"}, "records": []})
    )
    proc = run(mixed)
    out = json.loads(proc.stdout)
    assert proc.returncode == 1 and out["ok"] is False and "mismatch" in out["error"]


def test_precision_failures_score_retail_classes_not_wrong_object():
    """PR #60 review: H5 in retail is scored on extra_item/misplaced/
    wrong_slot (RS-7), NOT the desk-only wrong_object (which never fires
    in retail — a vacuous 0). The cell tallies the held-out precision
    classes; the CLI totals them per class so a nonzero count is never
    hidden behind wrong_object_total: 0."""
    c = cell(record("W", "S3", failures={"wrong_slot": 7, "missing_item": 1}))
    assert c["precision_failures"] == {"wrong_slot": 7}  # missing_item is not precision
    assert c["precision_failures_total"] == 7
    assert c["wrong_object_total"] == 0  # the vacuous desk metric, still reported

    clean = cell(record("L", "S1", failures={"timeout": 4}))
    assert clean["precision_failures"] == {} and clean["precision_failures_total"] == 0


def test_cli_totals_precision_failures_by_class(tmp_path):
    """The CLI surfaces holdout_precision_failures_total + by_class so the
    H5 signal is visible at the top level (PR #60 review)."""
    (tmp_path / "h3_results.json").write_text(
        json.dumps({"ok": True, "treatment": TREATMENT, "records": []})
    )
    for arm, tier, fails in (("W", "S3", {"wrong_slot": 7}), ("W", "S2", {"misplaced": 1})):
        d = tmp_path / f"arm_{arm}" / tier
        d.mkdir(parents=True)
        (d / "scenario.json").write_text(json.dumps(record(arm, tier, failures=fails)))
        (d / "token_samples.jsonl").write_text('{"wall_s": 5.0, "tokens": 1000}\n')
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "h3_analysis.py"), "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    out = json.loads(proc.stdout)
    assert out["holdout_precision_failures_total"] == 8
    assert out["holdout_precision_failures_by_class"] == {"wrong_slot": 7, "misplaced": 1}


def test_markdown_marks_leak_and_stop_reason():
    cells = [
        cell(record("W", "S2", prior=["s1-driver-v2"], holdout_ok=False)),
        cell(record("L", "S2")),
    ]
    text = results_markdown(cells, h3_verdict(cells))
    assert "wipe_leak" in text and "agent_done" in text
