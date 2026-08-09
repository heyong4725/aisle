"""--perception on the rollout path (HAR-1, TC-9).

The rung is DECLARED in the graph, where the graph hash attests it, and
rollout scrubs AISLE_PERCEPTION from the ambient environment for exactly
that reason (TC-9) — so the flag cannot inject a rung. Its job is the
inverse: refuse a run whose graph would not measure what the caller asked
for, before any gate runs, budget is reserved, or process is spawned.
"""

import pytest
import yaml
from cli_helpers import REPO_ROOT, run_json

from aisle.harness.rollout import perception_check, rollout

pytestmark = pytest.mark.unit

T0 = REPO_ROOT / "graphs" / "expert_t0.yaml"
T1 = REPO_ROOT / "graphs" / "expert_t1.yaml"


def test_declared_rungs_are_read_from_the_graphs():
    """TC-9: expert_t0 declares nothing and is L0 by definition; expert_t1
    declares L1 on its sim bridge. The check returns the DECLARED rung so
    rollout can record it in the run manifest — a result must attest which
    pose source produced it without reference to the graph file."""
    assert perception_check(REPO_ROOT, T0, None) == {"ok": True, "rung": "L0"}
    assert perception_check(REPO_ROOT, T0, "L0") == {"ok": True, "rung": "L0"}
    assert perception_check(REPO_ROOT, T1, None) == {"ok": True, "rung": "L1"}
    assert perception_check(REPO_ROOT, T1, "L1") == {"ok": True, "rung": "L1"}


def test_mismatch_is_refused_naming_both_rungs():
    """HAR-1: the refusal names the asserted and the declared rung, so the
    one-edit fix (pick the right graph) is legible."""
    result = perception_check(REPO_ROOT, T0, "L1")
    assert result["ok"] is False and result["gate"] == "perception"
    # gate refusals carry `detail` (the run_gates convention) so ledger and
    # campaign tooling can read every refusal through one key
    assert "L1" in result["detail"] and "L0" in result["detail"]


def test_an_unreadable_rung_cannot_be_asserted(tmp_path):
    """TC-9's refuse-don't-guess rule extends to the flag: a graph whose
    declared rung is unrecognized forbids nothing at validate time and would
    'match' nothing honestly here — asserting a rung against it is refused
    rather than compared against the strictest-assumed fallback."""
    doc = yaml.safe_load(T1.read_text())
    for node in doc["nodes"]:
        if node["id"] == "dora-genesis":
            node["env"]["AISLE_PERCEPTION"] = "L9"
    bad = tmp_path / "bad_rung.yaml"
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))

    result = perception_check(REPO_ROOT, bad, "L1")
    assert result["ok"] is False and result["gate"] == "perception"


def test_rollout_refuses_mismatch_before_touching_disk():
    """HAR-1: a refused run leaves no run directory behind and never reaches
    run_gates. (The before-the-budget-ledger ordering also holds, but is
    pinned by code position only — this test runs env_baseline=local, which
    skips reservation unconditionally, so it cannot distinguish the order;
    round-2 review note.)"""
    run_id = "perception-flag-refusal-unit"
    result = rollout(
        root=REPO_ROOT,
        graph=T0,
        tier="T1",
        episodes=1,
        seeds=[0],
        reset_mode="teleport",
        verifier="oracle",
        run_id=run_id,
        branch="b",
        no_idea_gate=True,
        env_baseline="local",
        perception="L1",
    )
    assert result["ok"] is False
    assert result["refused"]["gate"] == "perception"
    assert not (REPO_ROOT / "runs" / run_id).exists()


def test_cli_wires_the_flag_and_reports_json():
    """CON-8: the CLI surface — JSON to stdout, exit 0 iff ok — carries the
    perception refusal like every other gate."""
    code, report = run_json(
        "aisle.harness.cli",
        "rollout",
        "--graph",
        str(T0),
        "--tier",
        "T1",
        "--episodes",
        "1",
        "--seeds",
        "0",
        "--perception",
        "L1",
        "--no-idea-gate",
        "--env-baseline",
        "local",
        "--root",
        str(REPO_ROOT),
    )
    assert code != 0
    assert report["ok"] is False
    assert report["refused"]["gate"] == "perception"
