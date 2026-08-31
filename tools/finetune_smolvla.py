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


def build_frame_cache(run_dir: Path, frame_indices: set, size: int = 256):
    """ADR-45 pre-decode: ONE sequential pass over the mp4, keeping only
    the training frames, downscaled to the training size — the recorded
    33 h wall was a random seek per step. Disk-cached under the run,
    keyed by the video and the sorted index set (a ladder's doses share
    one cache). Returns {frame_idx: uint8 (size,size,3)}."""
    import hashlib

    import cv2
    import numpy as np

    video = run_dir / "traces" / "overhead.mp4"
    wanted = sorted(frame_indices)
    key = hashlib.sha256(
        (f"{video.stat().st_size}:{size}:" + ",".join(map(str, wanted))).encode()
    ).hexdigest()[:16]
    cache_path = run_dir / "traces" / f"frame_cache_{key}.npz"
    if cache_path.exists():
        data = np.load(cache_path)
        return {int(k): data[k] for k in data.files}
    cap = cv2.VideoCapture(str(video))
    out: dict = {}
    want = set(wanted)
    idx, last = 0, wanted[-1]
    while idx <= last:
        ok, bgr = cap.read()
        if not ok:
            break
        if idx in want:
            rgb = np.asarray(bgr[:, :, ::-1], dtype=np.uint8)
            out[idx] = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA)
        idx += 1
    cap.release()
    np.savez_compressed(cache_path, **{str(k): v for k, v in out.items()})
    print(
        f"[finetune] frame cache: {len(out)}/{len(wanted)} frames -> {cache_path.name}",
        file=sys.stderr,
    )
    return out


def load_tuples(dataset: Path, run_dir: Path, stride: int, limit: int | None):
    """(frame, action) pairs subsampled by stride across all episodes."""
    from aisle.harness.traces import query

    out = []
    for ep_file in sorted(dataset.glob("ep_*.json")):
        rec = json.loads(ep_file.read_text())
        cmds = query(run_dir, "joint_cmd_safe", episode=int(rec["episode"]))
        grips = query(run_dir, "gripper_cmd_safe", episode=int(rec["episode"]))
        grip_ts = grips["sim_time_ns"]
        states = query(run_dir, "joint_state", episode=int(rec["episode"]))
        sts, sdata = states["sim_time_ns"], states["data"]
        for i in range(0, rec["n_actions"], stride):
            ts = rec["action_ts"][i]
            gi = max((j for j, g in enumerate(grip_ts) if g <= ts), default=None)
            if gi is None or cmds["data"][i] is None:
                continue
            out.append(
                {
                    "frame_idx": rec["frame_indices"][i],
                    "state6": [
                        float(x)
                        for x in (
                            (
                                sdata[max((j for j, s in enumerate(sts) if s <= ts), default=0)]
                                or [0.0] * 6
                            )[:6]
                        )
                    ],
                    "action": list(cmds["data"][i])[:5] + [float(grips["data"][gi][0])],
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
    parser.add_argument(
        "--checkpoint-at",
        default="",
        help="comma-separated step numbers to dump checkpoints at (ADR-45 dose ladder)",
    )
    parser.add_argument("--resume", type=Path, default=None, help="checkpoint.pt to resume from")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--limit", type=int, default=4000)
    parser.add_argument("--lr", type=float, default=1e-4)
    args = parser.parse_args()

    import numpy as np
    import torch
    from peft import LoraConfig, get_peft_model

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from aisle.nodes.vla_backend import MODEL_ID, PINNED_REVISION, load_smolvla

    device = "cpu"  # MPS crashes at load (backend fence); CPU fallback per protocol
    print(f"[finetune] device={device}", file=sys.stderr)
    policy = load_smolvla()
    stats = json.loads((args.dataset / "stats.json").read_text())
    mods = dict(policy.named_modules())
    with torch.no_grad():
        si = mods["normalize_inputs.buffer_observation_state"]
        si.mean.copy_(torch.tensor(stats["state_mean"]))
        si.std.copy_(torch.tensor(stats["state_std"]))
        for name in ("normalize_targets.buffer_action", "unnormalize_outputs.buffer_action"):
            am = mods[name]
            am.mean.copy_(torch.tensor(stats["action_mean"]))
            am.std.copy_(torch.tensor(stats["action_std"]))
    print("[finetune] stats injected", file=sys.stderr)
    lora = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=[
            n for n, m in policy.named_modules() if type(m).__name__ == "Linear" and "expert" in n
        ]
        or "all-linear",
        lora_dropout=0.05,
    )
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
    frame_cache = build_frame_cache(args.run, {row["frame_idx"] for row in tuples})
    rng = np.random.default_rng(0)
    losses = []
    start_step = 0
    if args.resume and args.resume.exists():
        ck = torch.load(args.resume, weights_only=False)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        rng.bit_generator.state = ck["rng"]
        losses = ck["losses"]
        start_step = ck["step"]
        print(f"[finetune] resumed at step {start_step}", file=sys.stderr)
    dumps = sorted(int(s) for s in args.checkpoint_at.split(",") if s.strip())

    def dump_checkpoint(step: int) -> None:
        ckdir = args.out / f"checkpoint_{step}"
        ckdir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(ckdir)
        import shutil as _sh

        _sh.copy(args.dataset / "stats.json", ckdir / "stats.json")
        torch.save(
            {
                "model": model.state_dict(),
                "opt": opt.state_dict(),
                "rng": rng.bit_generator.state,
                "losses": losses,
                "step": step,
            },
            args.out / "checkpoint.pt",
        )
        print(f"[finetune] checkpoint at step {step} -> {ckdir}", file=sys.stderr)

    for step in range(start_step, args.steps):
        row = tuples[int(rng.integers(len(tuples)))]
        frame = frame_cache.get(row["frame_idx"])
        if frame is None:
            continue
        import torch.nn.functional as F

        img = torch.from_numpy(frame.copy()).permute(2, 0, 1).float().unsqueeze(0) / 255.0
        img = F.interpolate(img, size=(256, 256), mode="bilinear", align_corners=False)
        width = len(row["action"])
        chunk = torch.tensor([row["action"]], device=device).unsqueeze(1)
        chunk = chunk.expand(1, getattr(model.config, "chunk_size", 50), width)
        batch = {
            "observation.state": torch.tensor([row["state6"]], device=device),
            "observation.images.camera1": img.to(device),
            "observation.images.camera2": torch.zeros(1, 3, 256, 256, device=device),
            "observation.images.camera3": torch.zeros(1, 3, 256, 256, device=device),
            "action": chunk.contiguous(),
            "task": [row["task"]],
        }
        out = model.forward(batch)
        loss = out.get("loss") if isinstance(out, dict) else out[0]
        if not torch.is_tensor(loss):
            print(json.dumps({"ok": False, "error": f"no loss in forward output {type(out)}"}))
            return 1
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(float(loss))
        if step % 50 == 0:
            print(f"[finetune] step {step}: loss {float(loss):.4f}", file=sys.stderr)
        if (step + 1) in dumps:
            dump_checkpoint(step + 1)

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
