"""Generic content-addressed fault injector and sealed-bank tooling on a
synthetic canary bank (FLT-1, FLT-2, FLT-3, FLT-5, FLT-7, FLT-11, FLT-13,
FLT-14, FLT-15, FLT-16; issue #348).

Production identities never appear here: the canary bank targets a
throwaway repository tree built in tmp_path with the real allowlist
layout, so coverage validation, deterministic selection, sham parity,
atomic coupled injection and rollback, target escapes, frozen-asset
integrity, sealed-ledger completeness, commitment and reveal verification,
exact replay, and fail-closed corruption are all exercised without a bank.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aisle.harness import fault_injector as fi

pytestmark = pytest.mark.unit

SP = "src/aisle/nodes/segmented_pose.py"
IK = "src/aisle/nodes/ik_trajectory.py"
GT = "src/aisle/nodes/grasp_topdown.py"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in fi.TARGET_ALLOWLIST:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\nQUANTILE = 0.85\nLIFT = 0.015\nGRIP = 0.035\n")
    for rel in fi.FROZEN_ASSETS:
        p = root / rel
        if rel.endswith(".py") or rel.endswith(".toml"):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# frozen {rel}\n")
        else:
            p.mkdir(parents=True, exist_ok=True)
            (p / "frozen.py").write_text("# frozen\n")
    return root


def _instance(oid, family, target, operator, **over) -> dict:
    base = {
        "opaque_id": oid,
        "family": family,
        "target": target,
        "operator": operator,
        "persistence": "persistent",
        "activation_rule": "from launch",
        "severity_ladder": ["one"],
        "expected_evidence": "trace",
        "degradation_metric": "episode_success",
        "repair_class": "restoration",
        "safety_review": "static allowlist",
        "calibration_state": "uncalibrated",
        "release_disposition": "private",
    }
    return {**base, **over}


def _bank(root: Path) -> dict:
    def rep(target, find, replace):
        return {
            "operator": "replace",
            "target": target,
            "find": find,
            "replace": replace,
        }

    instances = [
        _instance(
            "p1",
            "perception",
            SP,
            rep(SP, "QUANTILE = 0.85", ["QUANTILE = 0.55", "QUANTILE = 0.35"]),
            severity_ladder=["0.55", "0.35"],
        ),
        _instance(
            "p2",
            "perception",
            SP,
            rep(SP, "QUANTILE = 0.85", "QUANTILE = 0.35"),
            persistence="intermittent",
            repair_class="novel_repair",
        ),
        _instance(
            "d1",
            "decision",
            GT,
            rep(GT, "GRIP = 0.035", ["GRIP = 0.015", "GRIP = 0.005"]),
            severity_ladder=["a", "b"],
        ),
        _instance(
            "m1",
            "motion",
            IK,
            rep(IK, "LIFT = 0.015", ["LIFT = 0.005", "LIFT = 0.0"]),
            severity_ladder=["a", "b"],
        ),
        _instance("s1", "schema_metadata", GT, rep(GT, "GRIP = 0.035", "GRIP = None")),
        _instance(
            "c1",
            "clocking",
            SP,
            rep(SP, "LIFT = 0.015", "LIFT = -2.0"),
            repair_class="novel_repair",
        ),
        _instance("r1", "runtime", SP, rep(SP, "# src", "raise RuntimeError()  # src")),
        _instance(
            "x1",
            "coupled",
            SP,
            [rep(SP, "QUANTILE = 0.85", "QUANTILE = 0.35"), rep(IK, "LIFT = 0.015", "LIFT = 0.0")],
        ),
        _instance(
            "x2",
            "coupled",
            GT,
            [rep(GT, "GRIP = 0.035", "GRIP = 0.005"), rep(IK, "LIFT = 0.015", "LIFT = 0.0")],
        ),
        _instance(
            "sham", "sham", SP, {"operator": "sham", "target": SP}, repair_class="diagnosis_only"
        ),
    ]
    return {
        "schema_version": fi.BANK_SCHEMA,
        "bank_id": "canary-v1",
        "lifecycle_state": "draft",
        "clean_baseline_hash": fi.clean_baseline_hash(root),
        "injector_version": fi.INJECTOR_VERSION,
        "instances": instances,
    }


def test_manifest_coverage_and_repair_mix_are_mechanical(tmp_path):
    """FLT-1 / FLT-2 / FLT-3 / FLT-11: a complete bank validates; a missing
    family, no sham, one coupled instance, one novel-repair family, an
    unresolved field, or a target off the allowlist each fail closed."""
    root = _repo(tmp_path)
    bank = _bank(root)
    assert fi.validate_manifest(bank) == []
    no_runtime = copy.deepcopy(bank)
    no_runtime["instances"] = [i for i in no_runtime["instances"] if i["family"] != "runtime"]
    assert any("family missing: runtime" in e for e in fi.validate_manifest(no_runtime))
    no_sham = copy.deepcopy(bank)
    no_sham["instances"] = [i for i in no_sham["instances"] if i["family"] != "sham"]
    assert "no sham control" in fi.validate_manifest(no_sham)
    one_coupled = copy.deepcopy(bank)
    one_coupled["instances"] = [i for i in one_coupled["instances"] if i["opaque_id"] != "x2"]
    assert any("coupled" in e for e in fi.validate_manifest(one_coupled))
    one_novel = copy.deepcopy(bank)
    next(i for i in one_novel["instances"] if i["opaque_id"] == "c1")["repair_class"] = (
        "restoration"
    )
    assert any("novel-repair" in e for e in fi.validate_manifest(one_novel))
    unresolved = copy.deepcopy(bank)
    unresolved["instances"][0]["safety_review"] = "unresolved"
    assert any("unresolved field" in e for e in fi.validate_manifest(unresolved))
    escape = copy.deepcopy(bank)
    escape["instances"][0]["operator"]["target"] = "src/aisle/nodes/budget_guard.py"
    escape["instances"][0]["target"] = "src/aisle/nodes/budget_guard.py"
    assert any("outside allowlist" in e for e in fi.validate_manifest(escape))


def test_materialize_is_atomic_hash_checked_and_outside_the_worktree(tmp_path):
    """FLT-7 / FLT-11: receipts carry pre/postimage hashes; a severity index
    selects the ladder rung; a coupled transaction changes both targets or
    none; frozen assets are untouched; destinations inside the worktree, a
    dirty preimage, an escaping path, and an unknown operator are refused."""
    root = _repo(tmp_path)
    bank = _bank(root)
    clean = bank["clean_baseline_hash"]
    staging = tmp_path / "staging"
    p1 = bank["instances"][0]
    r0 = fi.materialize(root, staging / "p1-0", p1, clean_hash=clean, severity_index=0)
    r1 = fi.materialize(root, staging / "p1-1", p1, clean_hash=clean, severity_index=1)
    assert r0["edits"][0]["postimage"] != r1["edits"][0]["postimage"]
    assert "QUANTILE = 0.35" in (staging / "p1-1" / SP).read_text()
    assert r0["frozen_assets_identical"] and not r0["sham"]
    coupled = next(i for i in bank["instances"] if i["opaque_id"] == "x1")
    rc = fi.materialize(root, staging / "x1", coupled, clean_hash=clean)
    assert [e["changed"] for e in rc["edits"]] == [True, True]
    sham = next(i for i in bank["instances"] if i["opaque_id"] == "sham")
    rs = fi.materialize(root, staging / "sham", sham, clean_hash=clean)
    assert rs["sham"] and rs["edits"][0]["preimage"] == rs["edits"][0]["postimage"]
    with pytest.raises(fi.FaultInjectorError, match="inside the participant worktree"):
        fi.materialize(root, root / "staged", p1, clean_hash=clean)
    with pytest.raises(fi.FaultInjectorError, match="dirty preimage"):
        fi.materialize(root, staging / "dirty", p1, clean_hash="sha256:not-the-clean-hash")
    escaping = copy.deepcopy(p1)
    escaping["operator"]["target"] = "../outside.py"
    with pytest.raises(fi.FaultInjectorError, match="traversal"):
        fi.materialize(root, staging / "esc", escaping, clean_hash=clean)
    partial = copy.deepcopy(coupled)
    partial["operator"][1]["find"] = "NOT PRESENT"
    with pytest.raises(fi.FaultInjectorError, match="exactly once"):
        fi.materialize(root, staging / "partial", partial, clean_hash=clean)
    assert not (staging / "partial" / SP).exists()  # nothing written on refusal
    unknown = copy.deepcopy(p1)
    unknown["operator"]["operator"] = "patch"
    with pytest.raises(fi.FaultInjectorError, match="unknown operator"):
        fi.materialize(root, staging / "unk", unknown, clean_hash=clean)


def test_assignment_commitment_ledger_reveal_and_replay(tmp_path):
    """FLT-5 / FLT-13 / FLT-14: assignments derive deterministically from a
    hidden seed; the commitment reveals no identity; reveal reproduces every
    row and flags a tampered one; replay reconstructs each postimage and
    flags drift."""
    root = _repo(tmp_path)
    bank = _bank(root)
    seed = b"hidden-campaign-seed"
    cells = ["p1", "m1", "sham"]
    a = fi.assign(seed, "canary-v1", "block-1", "s-01", cells)
    assert a == fi.assign(seed, "canary-v1", "block-1", "s-01", cells)
    assert fi.assign(b"other", "canary-v1", "block-1", "s-01", cells)["proof"] != a["proof"]
    commit = fi.commitment(
        bank, "sha256:seed-commitment", "sha256:protocol", {"p1": 2, "m1": 2, "sham": 2}
    )
    assert commit.startswith("sha256:") and "p1" not in json.dumps(commit)
    ledger = []
    for session in ("s-01", "s-02", "s-03"):
        assignment = fi.assign(seed, "canary-v1", "block-1", session, cells)
        inst = next(i for i in bank["instances"] if i["opaque_id"] == assignment["cell"])
        receipt = fi.materialize(
            root, tmp_path / "stage" / session, inst, clean_hash=bank["clean_baseline_hash"]
        )
        ledger.append(
            fi.ledger_row(assignment, receipt, campaign_id="canary", status="materialized")
        )
    assert all(set(row["participant_view"]) == {"session_id"} for row in ledger)
    revealed = fi.reveal(ledger, bank, seed, "canary-v1", cells)
    assert revealed["ok"] and len(revealed["mapping"]) == 3
    assert {m["family"] for m in revealed["mapping"]} <= {"perception", "motion", "sham"}
    tampered = copy.deepcopy(ledger)
    tampered[0]["cell"] = "m1" if tampered[0]["cell"] != "m1" else "p1"
    assert fi.reveal(tampered, bank, seed, "canary-v1", cells)["ok"] is False
    replayed = fi.replay(root, tmp_path / "replay", bank, ledger, bank["clean_baseline_hash"])
    assert replayed["ok"] and replayed["replayed"] == 3
    drifted = copy.deepcopy(ledger)
    drifted[0]["receipt"]["edits"][0]["postimage"] = "sha256:drift"
    assert (
        fi.replay(root, tmp_path / "replay2", bank, drifted, bank["clean_baseline_hash"])["ok"]
        is False
    )


def test_lifecycle_is_monotonic_and_seal_requires_a_valid_bank(tmp_path):
    """FLT-16: draft -> calibration -> sealed -> ... one step at a time; a
    bank with a coverage gap cannot be sealed; no state moves backward."""
    root = _repo(tmp_path)
    bank = _bank(root)
    cal = fi.advance(bank, "calibration")
    with pytest.raises(fi.FaultInjectorError, match="monotonically"):
        fi.advance(cal, "draft")
    with pytest.raises(fi.FaultInjectorError, match="monotonically"):
        fi.advance(cal, "scoring")
    broken = copy.deepcopy(cal)
    broken["instances"] = [i for i in broken["instances"] if i["family"] != "sham"]
    with pytest.raises(fi.FaultInjectorError, match="cannot seal"):
        fi.advance(broken, "sealed")
    sealed = fi.advance(cal, "sealed")
    assert sealed["lifecycle_state"] == "sealed"


def test_bank_and_ledger_paths_are_not_tracked_in_the_repository():
    """FLT-4 / FLT-15: only commitments and schema versions are tracked; no
    bank manifest, ledger, or reveal key lives under the repository."""
    from cli_helpers import REPO_ROOT

    tracked = (REPO_ROOT / ".gitignore").read_text()
    assert "runs/" in tracked
    for pattern in ("bank.json", "sealed-ledger", "reveal-key"):
        hits = [
            p
            for p in REPO_ROOT.rglob(pattern + "*")
            if ".git" not in p.parts and "node_modules" not in p.parts
        ]
        assert hits == [], hits
