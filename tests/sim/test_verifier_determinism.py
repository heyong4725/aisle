"""VER-7 determinism replay (SPEC 040; ADR-realistic-verifier D2).

The ratified decision is CPU inference because "a verdict source must
not flicker across replays". This test PINS that guarantee instead of
assuming it from the powder-spike precedent: EVERY verifier model runs
twice on the committed golden frames — once more inside this process
and once in a SEPARATE process — and the FULL raw surface (logits AND
boxes for the detector, masks for the segmenter) must be bit-identical.
It is the acceptance gate for any future torch/transformers/model bump.

Marker `sim`: needs the pinned weights and the committed fixture. The
GATE is the COMBINED sim run (with the Genesis-importing calibration
test in the same process) — running this file alone hides the MPS
default-device inheritance that PR #103's review caught.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "verifier" / "golden_frames.npz"
PROMPT = "a photo of a medicine box"

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

# the child re-runs BOTH models in a fresh interpreter and reports a
# hash per raw surface, so the comparison is byte-wise across processes
_CHILD = """
import hashlib, json, sys
import numpy as np
sys.path.insert(0, {src!r})
from aisle.verifier.models import cpu_batch, cpu_inference, load_pinned

data = np.load({fixture!r}, allow_pickle=False)
image = data["delivered_rgb_overhead"]
out = {{}}

proc_i, model_i = load_pinned("identity")
with cpu_inference():
    inputs = cpu_batch(proc_i(text=[[{prompt!r}]], images=image, return_tensors="pt"))
    pred = model_i(**inputs)
out["logits"] = hashlib.sha256(pred.logits.numpy().tobytes()).hexdigest()
out["boxes"] = hashlib.sha256(pred.pred_boxes.numpy().tobytes()).hexdigest()

proc_s, model_s = load_pinned("segmenter")
point = [[[int(image.shape[1] // 2), int(image.shape[0] // 2)]]]
with cpu_inference():
    inputs = cpu_batch(proc_s(images=image, input_points=point, return_tensors="pt"))
    pred = model_s(**inputs)
out["masks"] = hashlib.sha256(pred.pred_masks.numpy().tobytes()).hexdigest()

sys.stdout.write(json.dumps(out))
"""


def _identity_raw(image):
    """(logits, boxes) — the detector's FULL raw surface (VER-7)."""
    from aisle.verifier.models import cpu_batch, cpu_inference, load_pinned

    processor, model = load_pinned("identity")
    with cpu_inference():  # the PROCESSOR also creates tensors (PR #103)
        inputs = cpu_batch(processor(text=[[PROMPT]], images=image, return_tensors="pt"))
        out = model(**inputs)
    return out.logits.numpy(), out.pred_boxes.numpy()


def _segmenter_raw(image):
    """masks — the segmenter's raw surface (VER-7)."""
    from aisle.verifier.models import cpu_batch, cpu_inference, load_pinned

    processor, model = load_pinned("segmenter")
    point = [[[int(image.shape[1] // 2), int(image.shape[0] // 2)]]]
    with cpu_inference():  # the SAM processor calls .numpy() internally
        inputs = cpu_batch(processor(images=image, input_points=point, return_tensors="pt"))
        out = model(**inputs)
    return out.pred_masks.numpy()


def _child_hashes():
    script = _CHILD.format(src=str(REPO_ROOT / "src"), fixture=str(FIXTURE), prompt=PROMPT)
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert child.returncode == 0, child.stderr[-3000:]
    return json.loads(child.stdout)


def test_models_are_bit_identical_within_a_process():
    """Both models, repeated in-process: logits, boxes AND masks stable."""
    data = np.load(FIXTURE, allow_pickle=False)
    image = data["delivered_rgb_overhead"]

    logits_a, boxes_a = _identity_raw(image)
    logits_b, boxes_b = _identity_raw(image)
    assert np.array_equal(logits_a, logits_b), "identity logits differ in-process"
    assert np.array_equal(boxes_a, boxes_b), "identity BOXES differ in-process"

    assert np.array_equal(_segmenter_raw(image), _segmenter_raw(image)), (
        "segmenter masks differ in-process"
    )


def test_models_are_bit_identical_across_processes():
    """The stronger half, for EVERY model and every raw surface: a fresh
    interpreter must reproduce the same bytes — this catches
    nondeterminism that only appears after a reload (thread pools, lazy
    kernels, default-device drift)."""
    data = np.load(FIXTURE, allow_pickle=False)
    image = data["delivered_rgb_overhead"]

    logits, boxes = _identity_raw(image)
    masks = _segmenter_raw(image)
    here = {
        "logits": hashlib.sha256(logits.tobytes()).hexdigest(),
        "boxes": hashlib.sha256(boxes.tobytes()).hexdigest(),
        "masks": hashlib.sha256(masks.tobytes()).hexdigest(),
    }
    there = _child_hashes()
    for surface, digest in here.items():
        assert there[surface] == digest, (
            f"{surface} differ ACROSS processes — the CPU-only determinism "
            "guarantee (D2) does not hold for this model/framework version"
        )


def test_models_are_cpu_resident_even_after_genesis():
    """D2 has no exceptions: CPU is forced during CONSTRUCTION. Genesis
    sets an MPS default device on init and `from_pretrained` inherits
    it — the combined sim gate is what exposed this (PR #103 review)."""
    from aisle.verifier.models import load_pinned

    for role in ("identity", "segmenter"):
        _, model = load_pinned(role)
        assert next(model.parameters()).device.type == "cpu", f"{role} not CPU-resident"
