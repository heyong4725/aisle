"""Pinned verifier model loading (VER-5/D1/D2/D3, SPEC 040).

`models.lock` is the attestation surface: exact HF revisions plus a
sha256 per snapshot file. Loading ALWAYS goes through `load_pinned`,
which passes the pinned revision and verifies file hashes before the
weights are used — an unpinned or drifted snapshot is a refusal, not a
warning (issue #38: an unattested judgment channel is worse than none).

Inference is CPU-only (D2): a verdict source must not flicker across
replays, and Metal inference is nondeterministic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCK_PATH = Path(__file__).with_name("models.lock")
# D2: every verifier model runs here, no exceptions — the device is not
# configurable, so no deployment can quietly reintroduce GPU flicker
DEVICE = "cpu"


class ModelPinError(RuntimeError):
    """A pinned snapshot is missing, drifted, or unverifiable."""


def load_lock(path: Path | None = None) -> dict:
    lock = json.loads((path or LOCK_PATH).read_text())
    if lock.get("lock_version") != 1:
        raise ModelPinError(f"unsupported models.lock version {lock.get('lock_version')!r}")
    return lock


def verify_snapshot(role: str, snapshot_dir: Path, lock: dict | None = None) -> None:
    """Every file the lock names must exist under `snapshot_dir` with a
    matching sha256. Extra files are permitted (HF caches metadata
    alongside); a MISSING or ALTERED pinned file refuses."""
    entry = (lock or load_lock())["models"][role]
    for name, expected in sorted(entry["files_sha256"].items()):
        matches = list(snapshot_dir.rglob(name))
        if not matches:
            raise ModelPinError(f"{role}: pinned file {name!r} missing from {snapshot_dir}")
        digest = hashlib.sha256(matches[0].read_bytes()).hexdigest()
        if digest != expected:
            raise ModelPinError(
                f"{role}: {name!r} sha256 {digest[:12]}… != pinned {expected[:12]}…"
            )


def snapshot_path(role: str, lock: dict | None = None) -> Path:
    """Fetch (or reuse) the pinned snapshot and verify it. Network use is
    confined here; tests exercise verification against fixtures."""
    from huggingface_hub import snapshot_download

    entry = (lock or load_lock())["models"][role]
    path = Path(snapshot_download(entry["repo"], revision=entry["revision"]))
    verify_snapshot(role, path, lock)
    return path


def load_pinned(role: str, lock: dict | None = None):
    """(processor, model) for a pinned role, on CPU, in eval mode."""
    import torch
    from transformers import AutoModelForMaskGeneration, AutoProcessor, Owlv2ForObjectDetection

    path = snapshot_path(role, lock)
    processor = AutoProcessor.from_pretrained(path)
    factory = Owlv2ForObjectDetection if role == "identity" else AutoModelForMaskGeneration
    model = factory.from_pretrained(path, dtype=torch.float32).to(DEVICE)
    model.eval()
    return processor, model
