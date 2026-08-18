"""Unit tests for the verifier-authoring bench's pure parts (ENPIRE
follow-up 3) — no models, no traces (CON-12)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import judge_bench as jb  # noqa: E402

pytestmark = pytest.mark.unit


def test_corpus_splits_dev_runs_from_holdout(tmp_path):
    """Prompt-calibration runs are dev FOREVER — iteration must never
    self-grade on them."""
    for name in ("review-reeval-ik-transfer-v2", "fresh-run"):
        d = tmp_path / name
        (d / "traces").mkdir(parents=True)
        (d / "traces" / "overhead.mp4").write_bytes(b"")
        (d / "episodes.jsonl").write_text(
            json.dumps({"episode": 0, "target_med": "ibuprofen", "status": "success"}) + "\n"
        )
    dev = jb.corpus_entries(tmp_path / "review-reeval-ik-transfer-v2")
    hold = jb.corpus_entries(tmp_path / "fresh-run")
    assert dev[0]["split"] == "dev" and hold[0]["split"] == "holdout"


def test_promotion_gate_requires_floor_and_zero_false_success():
    """The 10x asymmetry: a judge that invents a delivery fails
    regardless of aggregate agreement; the gate scores HOLDOUT only."""
    base = {"split": "holdout", "oracle_status": "fail", "vlm_status": "fail"}
    good = [dict(base) for _ in range(8)] + [
        {"split": "holdout", "oracle_status": "success", "vlm_status": "success"},
        {"split": "dev", "oracle_status": "success", "vlm_status": "fail"},  # ignored
    ]
    v = jb.bench_verdict(good)
    assert v["passes"] and v["agreement"] == 1.0 and v["holdout_episodes"] == 9
    liar = good[:8] + [{"split": "holdout", "oracle_status": "fail", "vlm_status": "success"}]
    v2 = jb.bench_verdict(liar)
    assert not v2["passes"] and v2["false_success"] == 1
    v3 = jb.bench_verdict([dict(base, vlm_status=None)])
    assert not v3["passes"]  # nothing judged is never a pass
