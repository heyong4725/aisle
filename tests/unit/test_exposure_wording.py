"""SPEC 470 SFE-14: the machine-readable occurrence audit of claim-bearing
safety wording classifies and rejects broader claims, keeps denials, and
requires the three separate statements."""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT

from aisle.harness import exposure_wording as w

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("Wrong-medicine rate stays zero by construction.", ("by_construction", "broader_claim")),
        (
            "The guard knows the identity of the carried box.",
            ("guard_knows_identity", "broader_claim"),
        ),
        ("The guard checks which box is held.", ("guard_knows_identity", "broader_claim")),
        ("Verifier detection prevents a wrong delivery.", ("verifier_prevents", "broader_claim")),
        (
            "Zero wrong-object events in 800 episodes prove misdelivery is impossible.",
            ("zero_proves_impossible", "broader_claim"),
        ),
        # denials and unrelated sentences
        (
            "Wrong-object safety is not by construction; it is measured.",
            ("by_construction", "denial"),
        ),
        ("The guard does not know identity.", ("guard_knows_identity", "denial")),
        (
            "A zero wrong-object count is an observation, not an impossibility.",
            ("zero_proves_impossible", "denial"),
        ),
        ("Reachability is deterministic by construction (CON-5).", None),
        ("The realistic verifier is imperfect by construction.", None),
        ("Genesis provides physics; no delivery is scored twice.", None),
    ],
)
def test_sentences_are_classified(sentence, expected):
    """SFE-14: broader claims are rejected, denials kept, unrelated text ignored."""
    assert w.classify_sentence(sentence) == expected


def test_audit_text_reports_line_and_excerpt():
    """SFE-14: every occurrence carries path, line, class, verdict, excerpt."""
    text = "Intro.\n\nThe wrong-medicine rate is zero by construction.\nMore.\n"
    rows = w.audit_text("docs/x.md", text)
    assert len(rows) == 1
    assert rows[0]["path"] == "docs/x.md" and rows[0]["line"] == 3
    assert rows[0]["class"] == "by_construction" and rows[0]["verdict"] == "broader_claim"


def test_required_statements_are_detected(tmp_path):
    """SFE-14: the three statements must appear separately on the surfaces."""
    root = tmp_path
    (root / "README.md").write_text(
        "Validated declared paths traverse the guard. "
        "Measured gateway interventions alter kinematically illegal proposals. "
        "The verifier counts observed semantic outcomes.\n"
    )
    report = w.audit(root)
    assert report["ok"] is True and report["missing_statements"] == []
    (root / "README.md").write_text("Validated declared paths traverse the guard.\n")
    report = w.audit(root)
    assert report["ok"] is False
    assert set(report["missing_statements"]) == {
        "interventions_kinematic",
        "verifier_counts_outcomes",
    }


def test_committed_surfaces_pass_the_audit():
    """SFE-14: the repository's claim-bearing surfaces carry no broader claim
    and state the three claims separately."""
    report = w.audit(Path(REPO_ROOT))
    assert report["rejected"] == [], report["rejected"]
    assert report["missing_statements"] == []
    assert report["ok"] is True
    assert "docs/paper/aisle-paper.md" in report["surfaces"]
