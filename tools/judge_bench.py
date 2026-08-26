"""judge_bench — the verifier-authoring bench (ENPIRE follow-up 3,
owner-approved 2026-08-18).

ENPIRE's agents synthesized their own reward functions; AISLE's
verifier is frozen for integrity. This bench is the controlled middle:
a LABELED CORPUS of recorded episodes (tail frame + goal + oracle
verdict) that judge PROPOSALS — model, prompt, fusion — iterate against
OFFLINE, with the oracle grading them and zero sim cost. Promotion of a
winning judge into a live verifier capability stays on the standard
evalcarded human-merge path (§9.4); this tool only measures.

Corpus discipline: episodes are split dev/holdout BY RUN, so prompt
iteration on dev never self-grades (the ik-transfer reeval run was
already used to calibrate prompts and is dev forever). Passing rule
(the 10x asymmetry, ADR-8/H5): agreement >= floor on holdout AND
false_success == 0 — a judge that invents deliveries fails outright.

CON-8: JSON to stdout, logs to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PASS_FLOOR = 0.8
# dev-forever runs: used to develop prompt styles (analysis/ver-vlm)
DEV_RUNS = {"review-reeval-ik-transfer-v2"}


def corpus_entries(run_dir: Path, split_hint: str | None = None) -> list[dict]:
    eps = run_dir / "episodes.jsonl"
    if not eps.exists() or not (run_dir / "traces" / "overhead.mp4").exists():
        return []
    split = split_hint or ("dev" if run_dir.name in DEV_RUNS else "holdout")
    out = []
    for line in eps.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out.append(
            {
                "run": str(run_dir),
                "episode": int(rec.get("episode", 0)),
                "target_med": rec.get("target_med")
                or (rec.get("goal") or {}).get("target_med")
                or "",
                "oracle_status": rec.get("status"),
                "failure": rec.get("failure"),
                "split": split,
            }
        )
    return out


def bench_verdict(rows: list[dict], floor: float = PASS_FLOOR) -> dict:
    """The promotion gate, computed on HOLDOUT rows only."""
    hold = [r for r in rows if r.get("split") == "holdout"]
    judged = [r for r in hold if r.get("vlm_status") is not None]
    agree = sum(1 for r in judged if r["vlm_status"] == r["oracle_status"])
    false_success = sum(
        1 for r in judged if r["vlm_status"] == "success" and r["oracle_status"] == "fail"
    )
    agreement = round(agree / len(judged), 3) if judged else None
    # success-recall (2B retry, 2026-08-26): a constant-fail judge scored
    # 0.8 on a failure-heavy holdout and "passed" — zero discriminative
    # power must never promote, so every oracle-success episode counts
    # and at least one must be recognized
    oracle_successes = [r for r in judged if r["oracle_status"] == "success"]
    recalled = sum(1 for r in oracle_successes if r["vlm_status"] == "success")
    success_recall = round(recalled / len(oracle_successes), 3) if oracle_successes else None
    return {
        "holdout_episodes": len(hold),
        "judged": len(judged),
        "agreement": agreement,
        "false_success": false_success,
        "success_recall": success_recall,
        "passes": bool(judged)
        and agreement is not None
        and agreement >= floor
        and false_success == 0
        and (success_recall or 0.0) > 0.0,
        "floor": floor,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="assemble the labeled corpus")
    b.add_argument("--runs", type=Path, nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("score", help="score a judge spec against the corpus")
    s.add_argument("--corpus", type=Path, required=True)
    s.add_argument("--prompt-style", default="calibrated")
    s.add_argument("--model", default="smolvlm-500m", help="key in vlm_judge.MODELS")
    s.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.cmd == "build":
        rows = []
        for run in args.runs:
            got = corpus_entries(run)
            rows.extend(got)
            print(f"[bench] {run.name}: {len(got)} episodes", file=sys.stderr)
        args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
        by_split: dict = {}
        for r in rows:
            by_split[r["split"]] = by_split.get(r["split"], 0) + 1
        print(
            json.dumps(
                {"ok": True, "corpus": str(args.out), "episodes": len(rows), "splits": by_split}
            )
        )
        return 0

    # score: reuse the vlm_judge machinery per row
    sys.path.insert(0, str(Path(__file__).parent))
    import vlm_judge as vj
    from transformers import AutoModelForImageTextToText, AutoProcessor

    model_id, revision = vj.MODELS[args.model]
    processor = AutoProcessor.from_pretrained(model_id, revision=revision)
    model = AutoModelForImageTextToText.from_pretrained(model_id, revision=revision)
    model.eval()
    import torch
    from PIL import Image

    rows = [json.loads(x) for x in args.corpus.read_text().splitlines() if x.strip()]
    for r in rows:
        frame = vj.tail_frame(Path(r["run"]), r["episode"])
        r["vlm_status"] = None
        if frame is None:
            continue
        image = Image.fromarray(frame)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {
                        "type": "text",
                        "text": vj.PROMPTS[args.prompt_style].format(
                            med=r["target_med"] or "requested"
                        ),
                    },
                ],
            }
        ]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = processor(text=prompt, images=[image], return_tensors="pt")
        with torch.no_grad():
            out_ids = model.generate(**inputs, max_new_tokens=6)
        text = processor.batch_decode(
            out_ids[:, inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )[0]
        r["vlm_status"] = vj.verdict_from_text(text)
        print(
            f"[bench] {Path(r['run']).name} ep{r['episode']}: "
            f"oracle={r['oracle_status']} vlm={r['vlm_status']}",
            file=sys.stderr,
        )
    if args.out:
        args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(
        json.dumps(
            {
                "ok": True,
                "prompt_style": args.prompt_style,
                "model": {"id": model_id, "revision": revision},
                "verdict": bench_verdict(rows),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
