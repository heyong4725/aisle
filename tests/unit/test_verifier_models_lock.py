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


def test_color_words_are_derived_from_catalogue_rgb():
    """VER-9 query vocabulary (measured 2026-08-07): a med is only ~21x19
    px in the overhead frame, so printed labels would render ~2 px tall
    and no detector can read them — but colour IS visible at that size.
    The word comes from the med's own catalogue RGB, so the verifier
    needs no scene-file changes."""
    from aisle.verifier.models import color_word, med_queries

    assert color_word([0.85, 0.20, 0.20]) == "red"  # amoxicillin
    assert color_word([0.20, 0.55, 0.85]) == "blue"  # cetirizine
    assert color_word([0.55, 0.30, 0.70]) == "purple"  # omeprazole
    assert color_word([0.25, 0.70, 0.35]) == "green"  # metformin
    assert color_word([0.95, 0.60, 0.10]) == "orange"  # ibuprofen
    assert color_word((0.9, 0.9, 0.9, 1.0)) == "white"  # alpha ignored

    colors = {"omeprazole": [0.55, 0.30, 0.70], "metformin": [0.25, 0.70, 0.35]}
    assert med_queries(["omeprazole", "metformin"], colors) == ["a purple box", "a green box"]


def test_identity_threshold_sits_between_the_measured_noise_and_signal():
    """The threshold is calibrated, not guessed: measured on the golden
    frames, a present target scores 0.1331 while in-ROI non-target noise
    peaks at 0.0199 (absent target: 0.0001). The operating point must
    separate those with margin on both sides."""
    import tomllib
    from pathlib import Path

    thresholds = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "src/aisle/verifier/thresholds.toml").read_text()
    )
    threshold = thresholds["realistic"]["identity_min_score"]
    assert 0.0199 < threshold < 0.1331, "threshold does not separate measured noise from signal"
    assert threshold >= 2 * 0.0199, "less than 2x margin above the measured noise ceiling"
    assert thresholds["realistic"]["grounding_min_score"] <= threshold
