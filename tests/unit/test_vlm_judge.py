"""Unit tests for the offline VLM judge's pure parts (next-phases §5.2,
issue #248/#250) — no torch, no transformers, no traces (CON-12)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import vlm_judge as vj  # noqa: E402

pytestmark = pytest.mark.unit


def test_verdicts_parse_strictly():
    assert vj.verdict_from_text(" Yes.") == "success"
    assert vj.verdict_from_text("no") == "fail"
    assert vj.verdict_from_text("maybe") is None  # refusal, never a guess


def test_fidelity_counts_agreement_and_error_classes():
    rows = [
        {"vlm_status": "success", "oracle_status": "success"},
        {"vlm_status": "fail", "oracle_status": "fail"},
        {"vlm_status": "success", "oracle_status": "fail"},
        {"vlm_status": None, "oracle_status": "success"},
    ]
    f = vj.fidelity(rows)
    assert f["judged"] == 3 and f["refusals"] == 1
    assert f["agreement"] == round(2 / 3, 3)
    assert f["false_success"] == 1 and f["false_fail"] == 0


def test_backbone_label_states_the_smolvla_correlation():
    """#250: the report must SAY when the judge shares the policy's
    vision family — fidelity vs SmolVLA runs is not an independence
    claim."""
    assert vj.backbone_label("expert_t1")["independent_of_policy"]
    assert not vj.backbone_label("eval_vla_SMOLVLA_t1")["independent_of_policy"]
