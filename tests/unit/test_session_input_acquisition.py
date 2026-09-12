"""MON-12/MON-13: proof acquisition follows the authenticated receipt index."""

import hashlib
import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault", [None, "identity", "artifact", "redirect", "file", "session", "count"]
)
def test_session_input_acquisition_retains_exact_index_and_separate_limits(
    tmp_path, monkeypatch, fault
):
    """MON-13: no omitted indexed bytes, unindexed reads, or weakened size/hash checks."""
    from aisle.harness import frontend_conformance as profiles
    from aisle.harness.matched_session import _digest

    payloads = {
        "admission.json": b"{}",
        "journal": b"x" * 4096,
        "authored/main.py": b"before",
        "final/main.py": b"after",
    }
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()}
    record = {
        "schema_version": "aisle.matched-session-evidence.v1",
        "artifacts": {name: hashes[name] for name in ("admission.json", "journal")},
        "snapshots": {
            phase: {"main.py": {"sha256": hashes[phase + "/main.py"]}}
            for phase in ("authored", "final")
        },
    }
    record["immutable_id"] = _digest(record)
    raw = json.dumps(record).encode()
    payloads["matched-session.json"] = raw
    for name, data in payloads.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # Unindexed worker originals remain retained but do not duplicate the proof.
    unindexed = tmp_path / "unindexed"
    unindexed.write_bytes(b"keep this original")
    largest = max(map(len, payloads.values()))
    monkeypatch.setattr(profiles, "MAX_INPUT_BYTES", largest - 1 if fault == "file" else largest)
    monkeypatch.setattr(
        profiles,
        "MAX_SESSION_BYTES",
        sum(map(len, payloads.values())) - (fault == "session"),
        raising=False,
    )
    monkeypatch.setattr(
        profiles, "MAX_SESSION_FILES", len(payloads) - 1 - (fault == "count"), raising=False
    )
    if fault == "identity":
        record["immutable_id"] = "sha256:" + "0" * 64
        (tmp_path / "matched-session.json").write_text(json.dumps(record))
    elif fault == "artifact":
        (tmp_path / "journal").write_bytes(b"y" * largest)
    elif fault == "redirect":
        (tmp_path / "journal").unlink()
        (tmp_path / "journal").symlink_to(unindexed)
    if fault is None:
        acquired = profiles.acquire_session_inputs(tmp_path)
        assert acquired == payloads
        assert sum(map(len, acquired.values())) > profiles.MAX_INPUT_BYTES
    else:
        with pytest.raises(ValueError):
            profiles.acquire_session_inputs(tmp_path)
    assert unindexed.read_bytes() == b"keep this original"
