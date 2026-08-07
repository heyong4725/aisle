"""VER-5/D1/D3 (SPEC 040): the pinned-weights attestation surface.

Pure — no network, no torch: the lock is data, and verification is
checked against fixture files (CON-12).
"""

import hashlib
import json

import pytest

from aisle.verifier.models import (
    DEVICE,
    LOCK_PATH,
    ModelPinError,
    load_lock,
    verify_snapshot,
)

pytestmark = pytest.mark.unit


def test_lock_pins_the_ratified_models_with_licenses():
    """D1: OWLv2 is the ratified identity detector; D6: a MobileSAM-class
    segmenter. D3: exact revisions + license recorded at pin time."""
    lock = load_lock()
    identity = lock["models"]["identity"]
    assert identity["repo"] == "google/owlv2-base-patch16-ensemble"
    segmenter = lock["models"]["segmenter"]
    assert "slimsam" in segmenter["repo"].lower()  # MobileSAM-class (D6)
    for role, entry in lock["models"].items():
        assert len(entry["revision"]) == 40, f"{role}: revision is not a full commit sha"
        assert entry["license"] == "apache-2.0", f"{role}: license not verified at pin time"
        assert entry["files_sha256"], f"{role}: no file hashes recorded"
        for name, digest in entry["files_sha256"].items():
            assert len(digest) == 64, f"{role}/{name}: not a sha256"


def test_inference_device_is_cpu_only():
    """D2: CPU is not configurable — a deployment must not be able to
    reintroduce Metal's nondeterministic verdicts."""
    assert DEVICE == "cpu"


def test_lock_is_committed_and_canonical_json():
    text = LOCK_PATH.read_text()
    assert json.loads(text)  # parses
    assert text.endswith("\n")


def _fixture_snapshot(tmp_path, payload: bytes = b"weights"):
    (tmp_path / "model.safetensors").write_bytes(payload)
    return {
        "lock_version": 1,
        "models": {
            "identity": {
                "repo": "r",
                "revision": "a" * 40,
                "license": "apache-2.0",
                "files_sha256": {"model.safetensors": hashlib.sha256(b"weights").hexdigest()},
            }
        },
    }


def test_verification_passes_on_the_pinned_bytes(tmp_path):
    lock = _fixture_snapshot(tmp_path)
    verify_snapshot("identity", tmp_path, lock)  # no raise
    (tmp_path / "extra_metadata.json").write_text("{}")
    verify_snapshot("identity", tmp_path, lock)  # extra files are fine


def test_altered_weights_refuse(tmp_path):
    """The whole point of the pin: swapped weights must not judge."""
    lock = _fixture_snapshot(tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ModelPinError, match="sha256"):
        verify_snapshot("identity", tmp_path, lock)


def test_missing_pinned_file_refuses(tmp_path):
    lock = _fixture_snapshot(tmp_path)
    (tmp_path / "model.safetensors").unlink()
    with pytest.raises(ModelPinError, match="missing"):
        verify_snapshot("identity", tmp_path, lock)


def test_unsupported_lock_version_refuses(tmp_path):
    path = tmp_path / "models.lock"
    path.write_text(json.dumps({"lock_version": 99, "models": {}}))
    with pytest.raises(ModelPinError, match="unsupported"):
        load_lock(path)
