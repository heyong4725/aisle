"""SmolVLA backend (vla extra only; ADR-38). Isolated so the node file
and unit tests never import torch/lerobot (CON-12); loaded lazily at
the first inference. Weights identity: PINNED_REVISION is the HF
revision the manifest attests — the env-hash discipline extended to
weights (next-phases §5)."""

from __future__ import annotations

import numpy as np

MODEL_ID = "lerobot/smolvla_base"
PINNED_REVISION = "c83c3163b8ca9b7e67c509fffd9121e66cb96205"  # smolvla_base, pinned 2026-08-17
CHUNK_LEN = 10


def load_smolvla():
    """Loads the HUB config (feature schema included) with MPS masked
    off before lerobot's auto device selection runs: the MPS placement
    path crashes natively at weight loading on this stack (silent kill,
    leaked semaphore — measured 2026-08-20), while the same 450M loads
    cleanly on CPU. A default SmolVLAConfig() is NOT a substitute — it
    ships empty feature schemas and select_action refuses."""
    import torch

    torch.backends.mps.is_available = lambda: False  # crash fence, this process only
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

    policy = SmolVLAPolicy.from_pretrained(MODEL_ID, revision=PINNED_REVISION)
    policy.eval()
    return policy


def build_batch(policy, frames: dict, joint_state: np.ndarray, instruction: str):
    """The hub schema: observation.images.camera{1,2,3} at (3,256,256)
    and a 6-dim state (SO-100 native). Zero-shot embodiment adaptation,
    stated honestly: wrist frame -> camera1, overhead -> camera2,
    camera3 zeros; franka 9-dof state truncated to 6 (arm joints)."""
    import torch
    import torch.nn.functional as F

    def to_cam(key):
        if key not in frames:
            return torch.zeros(1, 3, 256, 256)
        raw, meta = frames[key]
        h, w = int(meta.get("h", 240)), int(meta.get("w", 320))
        img = torch.from_numpy(np.ascontiguousarray(raw.reshape(h, w, 3)))
        img = img.permute(2, 0, 1).float().unsqueeze(0) / 255.0
        return F.interpolate(img, size=(256, 256), mode="bilinear", align_corners=False)

    state = torch.from_numpy(np.asarray(joint_state[:6], dtype=np.float32)).unsqueeze(0)
    return {
        "observation.images.camera1": to_cam("wrist"),
        "observation.images.camera2": to_cam("overhead"),
        "observation.images.camera3": torch.zeros(1, 3, 256, 256),
        "observation.state": state,
        "task": [instruction],
    }


def select_chunk(
    policy, frames: dict, joint_state: np.ndarray, instruction: str, seed: int = 0
) -> list | None:
    """One inference -> a CHUNK of [q.., grip] rows (ADR-38). The model
    emits 6-dim SO-100 actions; mapped to [q(5 padded to n_dof-1), grip]
    by the caller's convention. Failures are logged then refused —
    a silent None hid a dead node for the whole m1 run (2026-08-20
    correction)."""
    import sys as _sys

    import torch

    try:
        # CON-5 (#268): flow matching SAMPLES during action selection —
        # seeded from the SIM stamp so same graph+seed+env reproduces
        torch.manual_seed(int(seed))
        batch = build_batch(policy, frames, joint_state, instruction)
        with torch.no_grad():
            actions = []
            for _ in range(CHUNK_LEN):
                a = policy.select_action(batch).squeeze(0).cpu().numpy()
                row = a.tolist()
                # pad model dims to the caller's [q.., grip] width
                width = len(joint_state) + 1
                if len(row) < width:
                    row = row[:-1] + [0.0] * (width - len(row)) + [row[-1]]
                actions.append(row[:width])
        return actions
    except Exception as exc:  # noqa: BLE001 — refusal, never a crash; LOUD
        print(f"[vla-backend] inference refused: {exc!r}", file=_sys.stderr)
        return None
