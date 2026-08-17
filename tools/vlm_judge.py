"""vlm-verifier v1 — OFFLINE judge (next-phases §5.2; issue #248 answer).

Replays recorded episodes through a small VLM ("is the <med> box in the
tray?") and reports fidelity against the oracle verdicts, with the
backbone-independence label the fidelity report requires (#250): the
judge's vision backbone (SmolVLM) is independent of the OWLv2-class
detector the realistic verifier uses AND of every classical pipeline —
but NOT of vla-policy-smolvla runs (SmolVLA is built on SmolVLM), which
the report states per run. CON-8: JSON to stdout, logs to stderr.

v1 scope: offline over `runs/<id>/` traces (rgb_overhead at the episode
tail + the episode's goal med + oracle status). The live sidecar node is
v2. Model pinned by HF revision below; lazy import (`vla` extra).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODEL_ID = "HuggingFaceTB/SmolVLM-500M-Instruct"
PINNED_REVISION = "a7da5b986cb59b408707209984f360a5f4ad7e47"  # pinned 2026-08-17
TAIL_S = 2.0  # judge from frames this close to the episode's end
# Two PRE-DECLARED prompt styles (reported side by side, never silently
# swapped): `semantic` asks in task language; `calibrated` grounds the
# question in the scene's visual reality the way the detector verifier
# is given the tray AABB — scene calibration, with the box-seeing left
# entirely to the model.
PROMPTS = {
    "semantic": (
        "Look at the robot workspace. Is the {med} medicine box inside "
        "the delivery tray? Answer with exactly one word: yes or no."
    ),
    "calibrated": (
        "This is an overhead view of a robot workstation. The delivery "
        "tray is the small white rectangular pad near the bottom of the "
        "image, below and left of the large white shelf. Is there a "
        "small colored box sitting on that white pad? Answer with "
        "exactly one word: yes or no."
    ),
}


def verdict_from_text(text: str) -> str | None:
    """'yes' -> success, 'no' -> fail, anything else -> refusal (None)."""
    word = text.strip().lower().rstrip(".!")
    if word.startswith("yes"):
        return "success"
    if word.startswith("no"):
        return "fail"
    return None


def fidelity(rows: list[dict]) -> dict:
    """Agreement stats over judged episodes; refusals counted apart."""
    judged = [r for r in rows if r["vlm_status"] is not None]
    agree = sum(1 for r in judged if r["vlm_status"] == r["oracle_status"])
    return {
        "episodes": len(rows),
        "judged": len(judged),
        "refusals": len(rows) - len(judged),
        "agreement": round(agree / len(judged), 3) if judged else None,
        "false_success": sum(
            1 for r in judged if r["vlm_status"] == "success" and r["oracle_status"] == "fail"
        ),
        "false_fail": sum(
            1 for r in judged if r["vlm_status"] == "fail" and r["oracle_status"] == "success"
        ),
    }


def backbone_label(graph_hint: str) -> dict:
    """#250 discipline: state whether this fidelity is an independence
    claim. SmolVLM shares its vision family with SmolVLA."""
    shared = "smolvla" in graph_hint.lower()
    return {
        "judge_backbone": "SmolVLM",
        "independent_of_policy": not shared,
        "note": (
            "policy is SmolVLA (same SmolVLM vision family): correlated estimates"
            if shared
            else "policy shares no backbone with the judge: independence claim valid"
        ),
    }


def episode_rows(run_dir: Path) -> list[dict]:
    eps = run_dir / "episodes.jsonl"
    if not eps.exists():
        raise SystemExit(json.dumps({"ok": False, "error": f"no episodes.jsonl in {run_dir}"}))
    return [json.loads(line) for line in eps.read_text().splitlines() if line.strip()]


def tail_frame(run_dir: Path, episode: int):
    """The last overhead frame INSIDE the episode window, decoded from
    traces/overhead.mp4. Frames are elided from the numeric trace path
    by design (the recorder routes big payloads out of Arrow); the mp4
    holds one video frame per recorded rgb event, so the rgb topic's
    sim_time_ns index maps event position -> video frame exactly. A
    small guard keeps clear of the next episode's reset teleport."""
    import cv2
    import numpy as np

    from aisle.harness.traces import episode_window, query

    t0, t1 = episode_window(run_dir, episode)
    stamps = query(run_dir, "rgb_overhead")["sim_time_ns"]
    guard_ns = 200_000_000
    hi = (t1 - guard_ns) if t1 is not None else stamps[-1] + 1
    idx = None
    for i, ts in enumerate(stamps):
        if t0 <= ts < hi:
            idx = i
    if idx is None:
        return None
    cap = cv2.VideoCapture(str(run_dir / "traces" / "overhead.mp4"))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame_bgr = cap.read()
    finally:
        cap.release()
    if not ok:
        return None
    return np.asarray(frame_bgr[:, :, ::-1], dtype=np.uint8)  # BGR -> RGB


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="runs/<id> directory")
    parser.add_argument("--graph-hint", default="", help="policy id for the backbone label")
    parser.add_argument("--out", type=Path, default=None, help="write rows JSONL here")
    parser.add_argument("--prompt-style", default="semantic", choices=sorted(PROMPTS))
    args = parser.parse_args()

    from transformers import AutoModelForImageTextToText, AutoProcessor

    processor = AutoProcessor.from_pretrained(MODEL_ID, revision=PINNED_REVISION)
    model = AutoModelForImageTextToText.from_pretrained(MODEL_ID, revision=PINNED_REVISION)
    model.eval()

    rows = []
    for rec in episode_rows(args.run):
        episode = int(rec.get("episode", 0))
        med = rec.get("target_med") or rec.get("goal", {}).get("target_med") or ""
        frame = tail_frame(args.run, episode)
        vlm_status = None
        if frame is not None:
            from PIL import Image

            image = Image.fromarray(frame)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {
                            "type": "text",
                            "text": PROMPTS[args.prompt_style].format(med=med or "requested"),
                        },
                    ],
                }
            ]
            prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
            inputs = processor(text=prompt, images=[image], return_tensors="pt")
            import torch

            with torch.no_grad():
                out_ids = model.generate(**inputs, max_new_tokens=6)
            text = processor.batch_decode(
                out_ids[:, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )[0]
            vlm_status = verdict_from_text(text)
        rows.append(
            {
                "episode": episode,
                "seed": rec.get("seed"),
                "target_med": med,
                "oracle_status": rec.get("status"),
                "vlm_status": vlm_status,
            }
        )
        print(
            f"[vlm-judge] ep {episode}: oracle={rec.get('status')} vlm={vlm_status}",
            file=sys.stderr,
        )

    if args.out:
        args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(
        json.dumps(
            {
                "ok": True,
                "run": str(args.run),
                "model": {"id": MODEL_ID, "revision": PINNED_REVISION},
                "prompt_style": args.prompt_style,
                "fidelity": fidelity(rows),
                "backbone": backbone_label(args.graph_hint),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
