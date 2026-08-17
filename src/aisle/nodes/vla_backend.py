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
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

    policy = SmolVLAPolicy.from_pretrained(MODEL_ID, revision=PINNED_REVISION)
    policy.eval()
    return policy


def select_chunk(policy, frames: dict, joint_state: np.ndarray, instruction: str) -> list | None:
    """One inference -> a CHUNK of [q.., grip] rows (ADR-38). Returns
    None when the backend cannot produce actions for this embodiment
    (recorded, never raised mid-episode)."""
    import torch

    try:
        wrist, wmeta = frames["wrist"]
        h, w = int(wmeta.get("h", 240)), int(wmeta.get("w", 320))
        image = torch.from_numpy(wrist.reshape(h, w, 3)).permute(2, 0, 1).float() / 255.0
        batch = {
            "observation.state": torch.from_numpy(joint_state).unsqueeze(0),
            "observation.image": image.unsqueeze(0),
            "task": [instruction],
        }
        with torch.no_grad():
            actions = []
            for _ in range(CHUNK_LEN):
                actions.append(policy.select_action(batch).squeeze(0).cpu().numpy().tolist())
        return actions
    except Exception:  # noqa: BLE001 — zero-shot bring-up: refusal, not a crash
        return None
