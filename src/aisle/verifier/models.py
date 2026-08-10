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

import contextlib
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


@contextlib.contextmanager
def cpu_inference():
    """Run a forward pass entirely on CPU (D2, PR #103 review). Moving
    the model AND the batch is still not enough: tensors CREATED inside
    the forward pass (position ids, masks, buffers) inherit torch's
    global default device, which Genesis sets to MPS — so outputs come
    back as MPS tensors. This context makes CPU the default for
    everything the pass constructs, and bundles no_grad."""
    import torch

    with torch.device(DEVICE), torch.no_grad():
        yield


def cpu_batch(inputs):
    """Move a processor batch to CPU (D2, PR #103 review). Forcing the
    MODEL to CPU is not enough: after Genesis sets an MPS default
    device, freshly-created input tensors land on MPS and inference dies
    ("Placeholder storage has not been allocated on MPS device"). Every
    inference site passes its batch through here, so the CPU guarantee
    covers inputs as well as weights."""
    return {k: (v.to(DEVICE) if hasattr(v, "to") else v) for k, v in dict(inputs).items()}


def load_pinned(role: str, lock: dict | None = None):
    """(processor, model) for a pinned role, on CPU, in eval mode.

    CPU is forced DURING construction, not after (PR #103 review): once
    Genesis initialises it sets an MPS default device, and
    `from_pretrained` inherits it — the model is then built on MPS
    (requiring accelerate, and reintroducing the nondeterminism D2
    exists to prevent) before any `.to("cpu")` could move it. The
    default-device context makes the CPU guarantee hold no matter what
    ran earlier in the process, which is exactly the combined-sim-gate
    case that caught this."""
    import torch
    from transformers import AutoModelForMaskGeneration, AutoProcessor, Owlv2ForObjectDetection

    path = snapshot_path(role, lock)
    processor = AutoProcessor.from_pretrained(path)
    factory = Owlv2ForObjectDetection if role == "identity" else AutoModelForMaskGeneration
    with torch.device(DEVICE):
        model = factory.from_pretrained(path, dtype=torch.float32)
    model = model.to(DEVICE)
    model.eval()
    assert next(model.parameters()).device.type == DEVICE, (
        f"{role}: model built on {next(model.parameters()).device}, not {DEVICE} (D2)"
    )
    return processor, model


# VER-9 query vocabulary (ADR section 7 legibility, measured 2026-08-07).
# A med occupies ~21x19 px in the overhead frame, so a printed label
# would render ~2 px tall — unreadable by any detector, which is why the
# planned texture pass cannot help at this camera geometry. What the
# model CAN see at 20 px is colour, and the queries are worded
# accordingly. Measured best score on the golden delivered frame:
#   "a photo of a {med}"                    0.036
#   "a photo of a {med} medicine box"       0.021
#   "a {colour} box"                        0.155   <- chosen
#   "a small {colour} cardboard box ..."    0.020
# The colour word is derived from the med's own catalogue RGB, so no
# scene file changes and the verifier stays the single source.
COLOR_WORDS = (
    ("red", (0.85, 0.15, 0.15)),
    ("orange", (0.95, 0.6, 0.1)),
    ("yellow", (0.95, 0.9, 0.2)),
    ("green", (0.25, 0.7, 0.35)),
    ("blue", (0.2, 0.55, 0.85)),
    ("purple", (0.55, 0.3, 0.7)),
    ("white", (0.95, 0.95, 0.95)),
    ("black", (0.05, 0.05, 0.05)),
)


def color_word(rgb) -> str:
    """The nearest basic colour name to a catalogue RGB — the identity
    query's discriminative feature at this resolution."""
    r, g, b = (float(c) for c in list(rgb)[:3])
    return min(
        COLOR_WORDS, key=lambda cw: sum((a - v) ** 2 for a, v in zip(cw[1], (r, g, b), strict=True))
    )[0]


def med_queries(med_names: list[str], med_colors: dict) -> list[str]:
    """VER-9 free-text queries, one per med class, in `med_names` order."""
    return [f"a {color_word(med_colors[name])} box" for name in med_names]


def detect_meds(image, med_names: list[str], model_pair=None, med_colors=None) -> list[dict]:
    """OWLv2 identity adapter (VER-9): image + med vocabulary ->
    [{label, score, box:[x0,y0,x1,y1]}] in PIXELS.

    The whole call — processor, forward, post-processing — runs inside
    the CPU default-device context (D2). Doing it here rather than at
    call sites is deliberate: the SAM/OWLv2 processors themselves create
    tensors on the default device, so a caller that merely moved the
    batch would still break under Genesis's MPS default (PR #103
    review)."""
    import torch

    processor, model = model_pair or load_pinned("identity")
    if med_colors is None:
        from aisle.scenes.pharmacy import load_meds

        med_colors = {name: spec["color"] for name, spec in load_meds().items()}
    queries = [med_queries(med_names, med_colors)]
    with cpu_inference():
        inputs = cpu_batch(processor(text=queries, images=image, return_tensors="pt"))
        outputs = model(**inputs)
        sizes = torch.tensor([[image.shape[0], image.shape[1]]], device=DEVICE)
        results = processor.post_process_grounded_object_detection(
            outputs=outputs, target_sizes=sizes, threshold=0.0
        )[0]
    return [
        {
            "label": med_names[int(label)],
            "score": float(score),
            "box": [float(v) for v in box],
        }
        for score, label, box in zip(
            results["scores"], results["labels"], results["boxes"], strict=True
        )
    ]


def segment_mask(image, point_xy, model_pair=None):
    """SlimSAM adapter (VER-11): image + a seed pixel -> the best mask as
    a boolean array. Same CPU-context discipline as detect_meds."""
    processor, model = model_pair or load_pinned("segmenter")
    point = [[[int(point_xy[0]), int(point_xy[1])]]]
    with cpu_inference():
        inputs = cpu_batch(processor(images=image, input_points=point, return_tensors="pt"))
        outputs = model(**inputs)
        masks = processor.image_processor.post_process_masks(
            outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu(),
        )[0]
        best = int(outputs.iou_scores.reshape(-1).argmax())
    return masks.reshape(-1, masks.shape[-2], masks.shape[-1])[best].numpy().astype(bool)
