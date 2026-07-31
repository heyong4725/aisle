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


def test_failure_classes_split_delivery_from_placement():
    """PR #60/#67 reviews: H5's 10x precision analogue is the wrong THING
    delivered (wrong_object desk, extra_item retail) — placement-quality
    failures (misplaced/wrong_slot/misaligned/overhang, §11.3) are a
    different claim and must never be conflated into one "precision"
    number (an earlier draft overstated an H5 breach exactly that way)."""
    c = cell(record("W", "S3", failures={"wrong_slot": 7, "extra_item": 1, "missing_item": 1}))
    assert c["delivery_failures"] == {"extra_item": 1}
    assert c["placement_failures"] == {"wrong_slot": 7}  # missing_item is neither
    assert c["delivery_failures_total"] == 1 and c["placement_failures_total"] == 7
    assert c["wrong_object_total"] == 0  # the vacuous desk metric, still reported

    clean = cell(record("L", "S1", failures={"timeout": 4}))
    assert clean["delivery_failures"] == {} and clean["placement_failures"] == {}


def test_cli_h5_totals_state_their_aggregation_set(tmp_path):
    """PR #60/#76 reviews: H5 totals are emitted over EXPLICIT cell sets
    — `selected` (highest-attempt clean cell per arm/tier, the verdict's
    set) and `all_records` (the full historical inventory including
    flagged/superseded cells) — each with the executed-episode exposure
    (episodes_scored), so a flagged cell's failures can never inflate
    the selected safety claim and an unexecuted cell contributes zero
    exposure."""
    (tmp_path / "h3_results.json").write_text(
        json.dumps({"ok": True, "treatment": TREATMENT, "records": []})
    )
    holdout_run = lambda tag, n: {"run_id": f"campaign-holdout-{tag}", "mtime": 1.0, "episodes": n}  # noqa: E731
    clean = record("W", "S3", failures={"wrong_slot": 7})
    clean["rollouts"] = [holdout_run("W-S3", 8)]
    flagged = record("W", "S2", prior=["s1-x"], failures={"misplaced": 1})
    flagged["rollouts"] = [holdout_run("W-S2", 3)]
    rerun = record("W", "S2", failures={"wrong_slot": 1})
    rerun["attempt"] = 2
    rerun["rollouts"] = [holdout_run("W-S2-r2", 8)]
    for rec, sub in ((clean, "S3"), (flagged, "S2"), (rerun, "S2-r2")):
        d = tmp_path / "arm_W" / sub
        d.mkdir(parents=True)
        (d / "scenario.json").write_text(json.dumps(rec))
        (d / "token_samples.jsonl").write_text('{"wall_s": 5.0, "tokens": 1000}\n')
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "h3_analysis.py"), "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    out = json.loads(proc.stdout)
    sel = out["h5"]["selected"]
    assert sorted(sel["cells"]) == ["W/S2-r2", "W/S3"]  # flagged W/S2 excluded
    assert sel["episodes_scored"] == 16
    assert sel["delivery_failures_total"] == 0
    assert sel["placement_failures_by_class"] == {"wrong_slot": 8}
    inv = out["h5"]["all_records"]
    assert inv["episodes_scored"] == 19
    assert inv["placement_failures_by_class"] == {"wrong_slot": 8, "misplaced": 1}


def test_treatment_drift_and_unattested_metric_from_provenance():
    """PR #76 review (L/S3-r2): a rollout recorded off the treatment pin
    (post-pin baseline merge) flags treatment_drift; a first-success
    supplied by a local/unattested skill-eval instead of a trusted
    rollout at the pin flags unattested_metric. Both are derived from
    the per-rollout provenance campaign_metrics now records; both
    exclude the cell from the verdict (ADR-h3: one pinned OID, trusted
    rollouts only)."""
    rec = record("L", "S3", first_success=1390.4)
    rec["rollouts"] = [
        {
            "run_id": "skill-eval-local",
            "mtime": 1.0,
            "episodes": 2,
            "pass1": 1.0,
            "git_sha": "abc123",
            "env_baseline": "local",
            "env_baseline_oid": None,
            "env_attested": None,
        },
        {
            "run_id": "trusted-but-drifted",
            "mtime": 2.0,
            "episodes": 4,
            "pass1": 1.0,
            "git_sha": "MERGEDHEAD",
            "env_baseline": "origin/main",
            "env_baseline_oid": "POSTPIN",
            "env_attested": True,
        },
        {"run_id": "campaign-holdout-L-S3-r2", "mtime": 3.0, "episodes": 8, "pass1": 0.0},
    ]
    c = cell(rec, "abc123")
    assert "treatment_drift" in c["flags"]
    assert "unattested_metric" in c["flags"]
    assert c["episodes_scored"] == 8

    # the same record with a trusted-at-pin success rollout is clean
    ok = record("L", "S3", first_success=100.0)
    ok["rollouts"] = [
        {
            "run_id": "dev",
            "mtime": 1.0,
            "episodes": 4,
            "pass1": 0.5,
            "git_sha": "abc123",
            "env_baseline": "origin/main",
            "env_baseline_oid": "abc123",
            "env_attested": True,
        },
    ]
    assert cell(ok, "abc123")["flags"] == []


def test_legacy_records_without_provenance_get_no_new_flags():
    """Campaign-1/2 scenario records predate rollout provenance: absent
    git_sha/env_baseline_oid must not be treated as drift (fail on what
    the record SHOWS, never on what it lacks — their original flags
    stand and their exclusion story is unchanged)."""
    rec = record("W", "S2", first_success=100.0)
    rec["rollouts"] = [
        {"run_id": "skill-eval", "mtime": 1.0, "episodes": 2, "pass1": 0.5},
        {"run_id": "campaign-holdout-W-S2", "mtime": 2.0, "episodes": 3, "pass1": 0.333},
    ]
    c = cell(rec, "abc123")
    assert "treatment_drift" not in c["flags"]
    assert "unattested_metric" not in c["flags"]
    assert c["episodes_scored"] == 3


def test_verdict_short_circuits_on_a_decided_false_tier():
    """PR #76 review: met = all(tiers), so one tier decided False from
    clean cells fixes NOT MET even while the other tier has no clean
    cell (pending stays only for undecided-and-incomplete)."""
    cells = [
        cell(record("W", "S2", first_success=None)),
        cell(record("L", "S2", first_success=None)),  # S2 decided False
        cell(record("W", "S3", first_success=100.0)),
        cell(record("L", "S3", first_success=100.0, drift=["x"])),  # S3 incomplete
    ]
    verdict = h3_verdict(cells)
    assert verdict["per_tier"] == {"S2": False}
    assert verdict["complete"] is False
    assert verdict["met"] is False

    undecided = [
        cell(record("W", "S2", first_success=100.0)),
        cell(record("L", "S2", first_success=40.0)),  # S2 True
        cell(record("W", "S3", first_success=100.0)),
        cell(record("L", "S3", first_success=100.0, drift=["x"])),  # S3 incomplete
    ]
    assert h3_verdict(undecided)["met"] is None  # nothing False, S3 pending


def test_markdown_renders_rerun_slot_identity():
    """PR #76 review: rerun rows must be distinguishable — the Cell
    column carries the runner's slot naming (S3-r2), not a bare tier."""
    rerun = record("L", "S3")
    rerun["attempt"] = 2
    cells = [cell(record("L", "S3")), cell(rerun)]
    text = results_markdown(cells, h3_verdict(cells))
    assert "| L | S3-r2 |" in text and "| L | S3 |" in text


def test_markdown_marks_leak_and_stop_reason():
    cells = [
        cell(record("W", "S2", prior=["s1-driver-v2"], holdout_ok=False)),
        cell(record("L", "S2")),
    ]
    text = results_markdown(cells, h3_verdict(cells))
    assert "wipe_leak" in text and "agent_done" in text


def test_residue_leak_derived_from_aggregates(tmp_path):
    """PR #67 review: an arm-L scenario that is not the FIRST arm-L
    scenario of its invocation and has no recorded residue-clear entry
    inherited the previous session's working state (the resume ran a
    pre-guard runner with wipes: []). Derived from the aggregates: the
    first L of an invocation is clean, an infra-abort entry is neither a
    flag target nor a residue-producing predecessor, and a recorded
    {"arm": "L", "before": <slot>} clear suppresses the flag."""
    (tmp_path / "h3_results.json").write_text(
        json.dumps(
            {
                "ok": True,
                "treatment": TREATMENT,
                "wipes": [],
                "records": [
                    {"arm": "L", "tier": "S1"},
                    {"arm": "L", "tier": "S2", "infra_error": "429"},
                ],
            }
        )
    )
    (tmp_path / "h3_results-r0.json").write_text(
        json.dumps(
            {
                "ok": True,
                "treatment": TREATMENT,
                "wipes": [{"arm": "L", "before": "S3"}],
                "records": [
                    {"arm": "L", "tier": "S2"},
                    {"arm": "L", "tier": "S3"},
                    {"arm": "L", "tier": "S1", "attempt": 2},
                ],
            }
        )
    )
    for arm, tier in (("L", "S1"), ("L", "S2"), ("L", "S3")):
        d = tmp_path / f"arm_{arm}" / tier
        d.mkdir(parents=True)
        (d / "scenario.json").write_text(json.dumps(record(arm, tier)))
    rerun = tmp_path / "arm_L" / "S1-r2"
    rerun.mkdir()
    rerun_rec = record("L", "S1")
    rerun_rec["attempt"] = 2
    (rerun / "scenario.json").write_text(json.dumps(rerun_rec))

    cells = {
        (c["arm"], c["tier"], c["attempt"]): c
        for c in map(cell, load_campaign(tmp_path)["records"])
    }
    assert "residue_leak" not in cells[("L", "S1", 1)]["flags"]  # first L of invocation 1
    assert "residue_leak" not in cells[("L", "S2", 1)]["flags"]  # first L of invocation 2
    assert "residue_leak" not in cells[("L", "S3", 1)]["flags"]  # cleared (wipes entry)
    assert "residue_leak" in cells[("L", "S1", 2)]["flags"]  # third L, no clear entry


def test_no_deliverable_is_a_scored_zero_not_a_partial():
    """W/S2-r2 (rerun campaign): a session that produces NO deliverable
    is a legitimate experimental OUTCOME — there was nothing to score,
    so the held-out pass rate is 0 — distinct from an expired scoring
    window (infra partial). The cell scores 0.0, carries no_deliverable
    for honesty, is NOT flagged, and therefore completes the verdict.
    PR #76 review: the classification is STRUCTURAL and fail-closed —
    the runner's `outcome` field (or the legacy exact error template)
    plus ok=false, no scores, no failures, zero executed episodes;
    every other unsuccessful state stays a partial."""

    def no_deliv_rec():
        rec = record("W", "S2", first_success=None, holdout_ok=False)
        rec["holdout"].update(
            {
                "error": "no deliverable at graphs/agent_campaign.yaml",
                "pass1": None,
                "pass8": None,
                "failures": None,
            }
        )
        return rec

    c = cell(no_deliv_rec())
    assert c["flags"] == []
    assert c["holdout_pass1"] == 0.0
    assert c["no_deliverable"] is True
    assert c["episodes_scored"] == 0

    # the structured field alone is sufficient (new runner records)
    structured = no_deliv_rec()
    structured["holdout"]["error"] = None
    structured["holdout"]["outcome"] = "no_deliverable"
    assert cell(structured)["no_deliverable"] is True

    # an expired window stays a partial
    exp = cell(record("W", "S2", holdout_ok=False))
    assert "holdout_partial" in exp["flags"]

    # fail closed: prose merely CONTAINING the phrase is not the state —
    # a record with scores/failures or executed episodes stays a partial
    prose = record("W", "S2", holdout_ok=False, pass1=0.25)
    prose["holdout"]["error"] = "scorer crashed: no deliverable rollouts remained"
    assert "holdout_partial" in cell(prose)["flags"]
    contradictory = no_deliv_rec()
    contradictory["holdout"]["failures"] = {"timeout": 2}
    assert "holdout_partial" in cell(contradictory)["flags"]
    executed = no_deliv_rec()
    executed["rollouts"] = [{"run_id": "campaign-holdout-W-S2", "mtime": 1.0, "episodes": 3}]
    assert "holdout_partial" in cell(executed)["flags"]

    # verdict completes: both arms' S2 first-success null -> tier False
    cells = [
        c,
        cell(record("L", "S2", first_success=None)),
        cell(record("W", "S3", first_success=None)),
        cell(record("L", "S3", first_success=100.0)),
    ]
    verdict = h3_verdict(cells)
    assert verdict["per_tier"] == {"S2": False, "S3": True}
    assert verdict["met"] is False
