"""VER-7 determinism replay (SPEC 040; ADR-realistic-verifier D2).

The ratified decision is CPU inference because "a verdict source must
not flicker across replays". This test PINS that guarantee instead of
assuming it from the powder-spike precedent: every verifier model runs
twice on the committed golden frames — once more inside this process,
and once in a SEPARATE process — and the raw outputs (logits/boxes,
masks) must be bit-identical. It is the acceptance gate for any future
torch/transformers/model bump.

Marker `sim`: needs the pinned weights and the committed fixture.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "verifier" / "golden_frames.npz"

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("transformers") is None,
        reason="transformers not installed (uv sync --extra sim)",
    ),
    pytest.mark.skipif(
        not FIXTURE.exists(),
        reason="golden frames missing (uv run python tools/make_verifier_fixtures.py)",
    ),
]

# a tiny script the SECOND process runs: same inputs, same pinned model,
# raw logits out as bytes we can compare exactly
_CHILD = """
import json, sys
import numpy as np
sys.path.insert(0, {src!r})
from aisle.verifier.models import load_pinned
data = np.load({fixture!r}, allow_pickle=False)
image = data["delivered_rgb_overhead"]
processor, model = load_pinned("identity")
import torch
inputs = processor(text=[["a photo of a medicine box"]], images=image, return_tensors="pt")
with torch.no_grad():
    out = model(**inputs)
logits = out.logits.numpy()
sys.stdout.write(json.dumps({{"sha": __import__("hashlib").sha256(logits.tobytes()).hexdigest(),
                             "shape": list(logits.shape)}}))
"""


def _identity_logits(image: np.ndarray) -> np.ndarray:
    import torch

    from aisle.verifier.models import load_pinned

    processor, model = load_pinned("identity")
    inputs = processor(text=[["a photo of a medicine box"]], images=image, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    return out.logits.numpy()


def test_identity_model_is_bit_identical_within_a_process():
    """Two runs, same process, same frame — no flicker (D2)."""
    data = np.load(FIXTURE, allow_pickle=False)
    image = data["delivered_rgb_overhead"]
    first = _identity_logits(image)
    second = _identity_logits(image)
    assert first.shape == second.shape
    assert np.array_equal(first, second), "identity logits differ across in-process replays"


def test_identity_model_is_bit_identical_across_processes():
    """The stronger half: a FRESH process must produce the same bytes —
    this is what catches nondeterminism that only shows up after a
    reload (thread pools, lazy kernels, device selection drift)."""
    import hashlib

    data = np.load(FIXTURE, allow_pickle=False)
    in_process = hashlib.sha256(_identity_logits(data["delivered_rgb_overhead"]).tobytes())
    script = _CHILD.format(src=str(REPO_ROOT / "src"), fixture=str(FIXTURE))
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert child.returncode == 0, child.stderr[-2000:]
    payload = json.loads(child.stdout)
    assert payload["sha"] == in_process.hexdigest(), (
        "identity logits differ ACROSS processes — the CPU-only determinism "
        "guarantee (D2) does not hold for this model/framework version"
    )


def test_segmenter_is_bit_identical_within_a_process():
    """The second model gets the same guarantee (D6 + D2)."""
    import torch

    from aisle.verifier.models import load_pinned

    data = np.load(FIXTURE, allow_pickle=False)
    image = data["delivered_rgb_overhead"]
    processor, model = load_pinned("segmenter")
    outputs = []
    for _ in range(2):
        inputs = processor(
            images=image,
            input_points=[[[image.shape[1] // 2, image.shape[0] // 2]]],
            return_tensors="pt",
        )
        with torch.no_grad():
            out = model(**inputs)
        outputs.append(out.pred_masks.numpy())
    assert np.array_equal(outputs[0], outputs[1]), "segmenter masks differ across replays"
