"""Hashed candidate table behind the matched-development protocol.

MON-3 / MON-8: the executor admits a candidate id bound to a controller-owned
table, never free launch fields. CON-5: the binding is a content hash.
MON-8: every candidate names its artifacts; a missing one refuses admission.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from aisle.harness import candidates as cand

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _synthetic_root(tmp_path: Path, candidate_id: str = "t1-oracle") -> Path:
    root = tmp_path / "root"
    (root / "docs/monolithic").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "docs/monolithic/candidates.json", root / "docs/monolithic/candidates.json"
    )
    table = cand.read_candidates(root)[0]
    row = table["candidates"][candidate_id]
    for rel in cand.candidate_artifacts(row):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture")
    return root


def _v2(root: Path, candidate_id: str = "t1-oracle") -> dict:
    return {
        "schema_version": cand.DEVELOPMENT_SCHEMA_V2,
        "purpose": "expert_parity",
        "candidate": candidate_id,
        "candidates_sha256": cand.candidates_sha256(root),
        "seeds": [7],
        "run_ceiling": 1,
        "episode_ceiling": 1,
        "timeout_s": 30,
    }


def test_candidate_table_hash_is_the_file_content_hash():
    """CON-5 / MON-8: the binding is the SHA-256 of the committed table bytes."""
    raw = (ROOT / "docs/monolithic/candidates.json").read_bytes()
    assert cand.candidates_sha256(ROOT) == hashlib.sha256(raw).hexdigest()
    table = cand.read_candidates(ROOT)[0]
    assert set(table["candidates"]) == {"t1-oracle", "t1-l2-realistic"}
    assert table["candidates"]["t1-l2-realistic"]["verifier"] == "realistic"


def test_v2_protocol_resolves_candidate_and_derives_launch_fields(tmp_path):
    """MON-3: a v2 protocol binds the candidate id; derived fields come from the table."""
    root = _synthetic_root(tmp_path)
    normalized = cand.normalize_development(_v2(root), root)
    assert normalized["candidate"] == "t1-oracle"
    assert normalized["tier"] == "T1" and normalized["verifier"] == "oracle"
    assert normalized["typed_graph"] == "graphs/expert_t1.yaml"
    assert normalized["monolithic_module"] == "experts/monolithic/expert_t1.py"
    assert normalized["documents"]["interface_map"] == "docs/monolithic/interface-map.json"
    assert set(cand.retained_artifacts(normalized)) == set(cand.candidate_artifacts(normalized))
    assert cand.retained_artifacts(None) == [] and cand.retained_artifacts({"tier": "T1"}) == []
    # re-verification of the normalized form is stable
    assert cand.normalize_development(normalized, root) == normalized


@pytest.mark.parametrize(
    "mutation",
    ["stale_hash", "unknown_candidate", "missing_artifact", "free_field", "derived_drift"],
)
def test_v2_protocol_refuses_unbound_inputs(tmp_path, mutation):
    """MON-8 / CON-5: stale table hash, unknown id, absent artifact, free launch
    fields and drifted derived fields all refuse rather than defaulting."""
    root = _synthetic_root(tmp_path)
    protocol = _v2(root)
    if mutation == "stale_hash":
        protocol["candidates_sha256"] = "0" * 64
    elif mutation == "unknown_candidate":
        protocol["candidate"] = "t9-nonexistent"
    elif mutation == "missing_artifact":
        (root / "graphs/turn_plans/expert_t1.json").unlink()
    elif mutation == "free_field":
        protocol["tier"] = "T2"
    else:
        protocol = cand.normalize_development(protocol, root)
        protocol["verifier"] = "realistic"
    with pytest.raises(cand.CandidateError):
        cand.normalize_development(protocol, root)


def test_candidate_missing_in_this_tree_refuses(tmp_path):
    """MON-8: the T1-L2 row is declared but its monolithic pair does not exist yet."""
    root = _synthetic_root(tmp_path, "t1-oracle")
    with pytest.raises(cand.CandidateError, match="artifact"):
        cand.normalize_development(_v2(root, "t1-l2-realistic"), root)


def test_v1_protocol_is_not_a_candidate_protocol():
    """MON-8: the fixed v1 protocol is handled by the executor's own check, not the table."""
    v1 = {
        "schema_version": cand.DEVELOPMENT_SCHEMA_V1,
        "purpose": "expert_parity",
        "tier": "T1",
        "embodiment": "franka",
        "verifier": "oracle",
        "reset": "teleport",
        "seeds": [7],
        "run_ceiling": 1,
        "episode_ceiling": 1,
        "timeout_s": 30,
    }
    with pytest.raises(cand.CandidateError, match="schema"):
        cand.normalize_development(v1, ROOT)


def test_normalized_protocol_key_order_is_fixed(tmp_path):
    """CON-5: the retained development form serializes identically across processes."""
    root = _synthetic_root(tmp_path)
    normalized = cand.normalize_development(_v2(root), root)
    assert list(normalized) == [*cand.V2_BASE_KEYS, *cand.DERIVED_KEYS]


def test_malformed_table_refuses(tmp_path):
    """CON-5: an unreadable or mis-shaped table cannot admit anything."""
    root = tmp_path / "root"
    (root / "docs/monolithic").mkdir(parents=True)
    (root / "docs/monolithic/candidates.json").write_text(json.dumps({"candidates": {}}))
    with pytest.raises(cand.CandidateError):
        cand.read_candidates(root)
