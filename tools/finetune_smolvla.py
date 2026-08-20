"""finetune_smolvla — the pre-registered MPS LoRA fallback experiment
(analysis/m1/finetune_protocol.md; owner-approved item 1).

Trains LoRA adapters on SmolVLA's action head from the exported
demonstration tuples (guard-clamped actions, causal frame pairing —
tools/demo_export.py) and saves the adapter for the eval graph's
backend to load via AISLE_VLA_ADAPTER. Single config, no sweep (the
protocol's rule: the first contrast is tuned-vs-zero-shot). CON-8:
JSON to stdout, logs to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def frame_for(run_dir: Path, frame_idx: int, cap_cache: dict):
    import cv2
    import numpy as np

    cap = cap_cache.get(run_dir)
    if cap is None:
        cap = cv2.VideoCapture(str(run_dir / "traces" / "overhead.mp4"))
        cap_cache[run_dir] = cap
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, bgr = cap.read()
    return np.asarray(bgr[:, :, ::-1], dtype=np.uint8) if ok else None


def load_tuples(dataset: Path, run_dir: Path, stride: int, limit: int | None):
    """(frame, action) pairs subsampled by stride across all episodes."""
    from aisle.harness.traces import query

    out = []
    for ep_file in sorted(dataset.glob("ep_*.json")):
        rec = json.loads(ep_file.read_text())
        cmds = query(run_dir, "joint_cmd_safe", episode=int(rec["episode"]))
        grips = query(run_dir, "gripper_cmd_safe", episode=int(rec["episode"]))
        grip_ts = grips["sim_time_ns"]
        for i in range(0, rec["n_actions"], stride):
            ts = rec["action_ts"][i]
            gi = max((j for j, g in enumerate(grip_ts) if g <= ts), default=None)
            if gi is None or cmds["data"][i] is None:
                continue
            out.append(
                {
                    "frame_idx": rec["frame_indices"][i],
                    "action": list(cmds["data"][i]) + [float(grips["data"][gi][0])],
                    "task": f"pick the {rec.get('target_med', 'requested')} box "
                    "from the shelf and place it in the tray",
                }
            )
            if limit and len(out) >= limit:
                return out
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="the demos' run dir (frames)")
    parser.add_argument("--out", type=Path, required=True, help="adapter output dir")
    parser.add_argument("--steps", type=int, default=800)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--limit", type=int, default=4000)
    parser.add_argument("--lr", type=float, default=1e-4)
    args = parser.parse_args()

    import numpy as np
    import torch
    from peft import LoraConfig, get_peft_model

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from aisle.nodes.vla_backend import MODEL_ID, PINNED_REVISION, load_smolvla

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"[finetune] device={device}", file=sys.stderr)
    policy = load_smolvla()
    lora = LoraConfig(r=16, lora_alpha=32, target_modules="all-linear", lora_dropout=0.05)
    model = get_peft_model(policy, lora)
    model.to(device)
    model.train()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"[finetune] trainable params: {trainable:,}", file=sys.stderr)

    tuples = load_tuples(args.dataset, args.run, args.stride, args.limit)
    print(f"[finetune] tuples: {len(tuples)}", file=sys.stderr)
    if not tuples:
        print(json.dumps({"ok": False, "error": "no training tuples"}))
        return 1

    opt = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.lr)
    cap_cache: dict = {}
    rng = np.random.default_rng(0)
    losses = []
    for step in range(args.steps):
        row = tuples[int(rng.integers(len(tuples)))]
        frame = frame_for(args.run, row["frame_idx"], cap_cache)
        if frame is None:
            continue
        batch = {
            "observation.state": torch.zeros(1, len(row["action"]) - 1, device=device),
            "observation.image": torch.from_numpy(frame.copy())
            .permute(2, 0, 1)
            .float()
            .unsqueeze(0)
            .to(device)
            / 255.0,
            "action": torch.tensor([row["action"]], device=device),
            "task": [row["task"]],
        }
        loss = model.forward(batch)[0] if hasattr(model, "forward") else None
        if not torch.is_tensor(loss):
            loss = loss.get("loss") if isinstance(loss, dict) else None
        if loss is None:
            print(json.dumps({"ok": False, "error": "policy forward returned no loss"}))
            return 1
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(float(loss))
        if step % 50 == 0:
            print(f"[finetune] step {step}: loss {float(loss):.4f}", file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.out)
    (args.out / "training_record.json").write_text(
        json.dumps(
            {
                "base": {"id": MODEL_ID, "revision": PINNED_REVISION},
                "steps": args.steps,
                "tuples": len(tuples),
                "loss_first": losses[0] if losses else None,
                "loss_last": sum(losses[-20:]) / max(1, len(losses[-20:])),
                "device": device,
                "lr": args.lr,
            }
        )
    )
    print(
        json.dumps(
            {
                "ok": True,
                "adapter": str(args.out),
                "loss_first": losses[0],
                "loss_last_20_mean": sum(losses[-20:]) / max(1, len(losses[-20:])),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
