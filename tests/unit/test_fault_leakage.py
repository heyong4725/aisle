"""SPEC 450 FLT-8: participant-visible normalization in the injector and
the frozen leakage probe over materialization receipts."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT

from aisle.harness import fault_injector as fi
from aisle.harness import fault_leakage as fl

pytestmark = pytest.mark.unit

PROBE = json.loads((Path(REPO_ROOT) / "analysis/fault-bank/leakage-probe.json").read_text())


def _receipt(sham: bool, size: int = 4096, mode: int = 0o644, mtime: int = fi.STAGED_MTIME, n=1):
    return {
        "sham": sham,
        "visible_metadata": {
            f"src/aisle/nodes/t{i}.py": {"size_bytes": size, "mode": mode, "mtime": mtime}
            for i in range(n)
        },
    }


def test_committed_probe_is_frozen_and_valid():
    """FLT-8: the probe declaration freezes allowlist, classifier, split and
    decision rule, and validates."""
    assert fl.validate_probe(PROBE) == []
    assert PROBE["feature_allowlist"] == ["staged_file_count", "size_bytes", "mode", "mtime"]


def test_probe_refuses_features_outside_the_allowlist():
    """FLT-8: a feature the declaration did not freeze is refused."""
    bad = copy.deepcopy(PROBE)
    bad["feature_allowlist"].append("wall_seconds")
    with pytest.raises(fl.LeakageProbeError, match="outside the frozen allowlist"):
        fl.probe_cells(bad, [_receipt(True)] * 3 + [_receipt(False)] * 3)


def test_identical_visible_metadata_is_not_distinguishable():
    """FLT-8: sham and fault cells with the same size/mode/mtime/count do
    not beat chance."""
    cells = [_receipt(True)] * 3 + [_receipt(False)] * 9
    report = fl.probe_cells(PROBE, cells)
    assert report["decision"] == "not_distinguishable", report
    assert report["counts"] == {"sham": 3, "fault": 9}
    assert report["permutation_p"] >= PROBE["decision_rule"]["alpha"]


def test_size_leak_blocks_the_bank():
    """FLT-8: if fault cells are systematically larger than sham cells the
    classifier beats the permutation null and the decision is blocked."""
    cells = [_receipt(True, size=4096)] * 4 + [_receipt(False, size=4096 + 7)] * 8
    report = fl.probe_cells(PROBE, cells)
    assert report["decision"] == "blocked", report
    assert report["balanced_accuracy"] == 1.0
    assert report["permutation_p"] < PROBE["decision_rule"]["alpha"]


def test_too_few_cells_per_class_is_blocked_not_passed():
    """FLT-8: one sham cannot establish chance; the bank stays blocked."""
    report = fl.probe_cells(PROBE, [_receipt(True)] + [_receipt(False)] * 10)
    assert report["decision"] == "blocked" and "fewer than" in report["reason"]


def test_probe_is_deterministic_for_a_seed():
    """CON-5: the same receipts and probe give the same p-value."""
    cells = [_receipt(True, size=4096 + i % 2) for i in range(4)] + [
        _receipt(False, size=4096 + i % 2) for i in range(8)
    ]
    a = fl.probe_cells(PROBE, cells)
    b = fl.probe_cells(PROBE, cells)
    assert a == b


def test_receipt_without_visible_metadata_is_refused():
    """FLT-8: v1 receipts (no normalization) cannot be probed silently."""
    with pytest.raises(fl.LeakageProbeError, match="visible_metadata"):
        fl.features({"sham": True}, PROBE["feature_allowlist"])


# -- the injector's normalization ------------------------------------------


def test_padding_equalizes_sham_and_fault_sizes():
    """FLT-8 / FLT-7: every staged target is padded to preimage + PAD_BYTES,
    so a shorter or longer replacement and a sham have the same size; an
    edit past the pad is refused."""
    before = b"LIFT_H = 0.015\n"
    for after in (before, b"LIFT_H = 0.0\n", b"LIFT_H = 0.0125  # x\n"):
        padded = fi.pad_to_visible_size(before, after)
        assert len(padded) == len(before) + fi.PAD_BYTES
        assert padded.startswith(after)
        assert padded[len(after) :].startswith(b"\n#")
    with pytest.raises(fi.FaultInjectorError, match="visible-size pad"):
        fi.pad_to_visible_size(before, before + b"#" * fi.PAD_BYTES)


def _clean_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src/aisle/nodes").mkdir(parents=True)
    for rel in fi.FROZEN_ASSETS:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(f"# frozen {rel}\n")
    target = root / "src/aisle/nodes/ik_trajectory.py"
    target.write_text("LIFT_H = 0.015\nGRIP_STEP_PER_TICK = 0.010\n")
    os.chmod(target, 0o644)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "clean"],
        cwd=root,
        check=True,
    )
    return root


def _instance(oid: str, operator: dict) -> dict:
    return {
        "opaque_id": oid,
        "family": "motion",
        "target": "src/aisle/nodes/ik_trajectory.py",
        "operator": operator,
        "persistence": "persistent",
        "activation_rule": "from launch",
        "severity_ladder": ["x"],
        "expected_evidence": "x",
        "degradation_metric": "episode_success",
        "repair_class": "restoration",
        "safety_review": "static",
        "calibration_state": "candidate",
        "release_disposition": "private",
    }


def test_materialize_records_identical_visible_metadata_for_sham_and_fault(tmp_path):
    """FLT-8: after materialization a sham and a fault of the same target
    have identical size, mode and mtime in the receipt."""
    root = _clean_repo(tmp_path)
    clean = fi.clean_baseline_hash(root)
    sham = fi.materialize(
        root, tmp_path / "s", _instance("sham", {"operator": "sham"}), clean_hash=clean
    )
    fault = fi.materialize(
        root,
        tmp_path / "f",
        _instance(
            "f", {"operator": "replace", "find": "LIFT_H = 0.015", "replace": "LIFT_H = 0.0"}
        ),
        clean_hash=clean,
    )
    assert sham["sham"] is True and fault["sham"] is False
    assert sham["visible_metadata"] == fault["visible_metadata"]
    meta = next(iter(sham["visible_metadata"].values()))
    assert meta["mtime"] == fi.STAGED_MTIME and meta["mode"] == 0o644
    assert meta["size_bytes"] == len(b"LIFT_H = 0.015\nGRIP_STEP_PER_TICK = 0.010\n") + fi.PAD_BYTES
    staged = (tmp_path / "f/src/aisle/nodes/ik_trajectory.py").read_text()
    assert staged.startswith("LIFT_H = 0.0\n")
    compile(staged, "staged.py", "exec")  # the pad is a valid trailing comment
    assert fl.probe_cells(PROBE, [sham] * 3 + [fault] * 3)["decision"] == "not_distinguishable"
