"""paper_media — the committed illustration clips, cut deterministically
from recorded run videos (the derived-artifact discipline for media).

A clip is an ILLUSTRATION pointing at its run record, never evidence —
the record is the evidence (this repo's founding complaint about demo
culture). Each clip is defined by (run_id, episode range) and cut from
the run's `traces/overhead.mp4` using the measured video-time = 3.000 x
sim-time mapping (calibrated across three runs, residual < 1 s from
reset frames), sped up for viewing, scaled to 640 px, CRF 30. The
manifest names every clip's run, seeds, sim window, and speed factor.
CON-8: JSON manifest on stdout, logs stderr, exit 0 iff all clips cut.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "media"
SIM_TO_VIDEO = 3.0  # measured: video seconds per sim second (see docstring)

# (name, run_id, first_episode, n_episodes, speed, caption_facts)
CLIPS = [
    (
        "t1_expert_pick",
        "hybrid-t1-noregression-r2",
        0,
        1,
        2,
        "T1 expert baseline: named-med pick, seed 30, 20.3 sim-s, success",
    ),
    (
        "t2_label_read_pick",
        "t2-scope-v2",
        4,
        1,
        3,
        "T2 registered stack: read-tour -> label read -> grasp, seed 12, 24.7 sim-s, success",
    ),
    (
        "t4_recovery_chain",
        "t4-inc2-recovery-r4",
        0,
        2,
        4,
        "T4 inc-2 seed 0: scripted misdelivery, correction, return-to-shelf, "
        "redelivery -- the full recovery chain (43.1 sim-s, both goals success)",
    ),
    (
        "m1_lockstep_grasp_drop",
        "m1-lockstep-n8",
        2,
        1,
        4,
        "M1 lockstep-eval seed 32: the fine-tuned VLA policy's only recorded "
        "pick behavior -- grasp then drop at 39 sim-s (episode scored dropped)",
    ),
]


def episode_window(run: Path, first: int, n: int) -> tuple[float, float, list]:
    rows = [json.loads(x) for x in (run / "episodes.jsonl").read_text().splitlines() if x.strip()]
    start_sim = sum(r["t_end"] for r in rows[:first])
    dur_sim = sum(r["t_end"] for r in rows[first : first + n])
    return start_sim * SIM_TO_VIDEO, dur_sim * SIM_TO_VIDEO, rows[first : first + n]


def cut(name: str, run_id: str, first: int, n: int, speed: int, caption: str) -> dict:
    run = REPO / "runs" / run_id
    src = run / "traces" / "overhead.mp4"
    start, dur, rows = episode_window(run, first, n)
    out = OUT / f"{name}.mp4"
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.2f}",
        "-t",
        f"{dur:.2f}",
        "-i",
        str(src),
        "-vf",
        f"setpts=PTS/{speed},scale=640:-2",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return {
        "clip": str(out.relative_to(REPO)),
        "run_id": run_id,
        "episodes": [{"seed": r["seed"], "status": r["status"], "t_end": r["t_end"]} for r in rows],
        "video_window_s": [round(start, 2), round(start + dur, 2)],
        "speed": f"{speed}x",
        "size_kb": out.stat().st_size // 1024,
        "caption": caption,
    }


def main() -> int:
    manifest = []
    for spec in CLIPS:
        try:
            entry = cut(*spec)
            manifest.append(entry)
            print(f"[media] {entry['clip']} ({entry['size_kb']} KB)", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            manifest.append({"clip": spec[0], "error": str(exc)})
            print(f"[media] FAIL {spec[0]}: {exc}", file=sys.stderr)
    ok = all("error" not in m for m in manifest)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"ok": ok, "clips": manifest}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
