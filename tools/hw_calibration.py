"""hw_calibration — the VER-8 hardware addendum, buildable today.

On hardware, the v1 calibration block comes from a MEASURED per-device
artifact (`env/calibration.toml`) instead of the scene files — fields,
conventions, and stage-0 refusal identical to sim (VER-8). This tool:

  --template   write the documented template (every measured field)
  --build      env/calibration.toml -> the v1 block (JSON stdout),
               via the SAME build_calibration_v1 the sim bridge uses
  --check      build, then run the SAME stage-0 refusal predicates
               against the block as its own nominal — a malformed or
               convention-violating artifact refuses BEFORE any robot
               runs (fail closed, day one)

CON-8: JSON stdout, logs stderr, exit 0 iff ok."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

TEMPLATE = """\
# env/calibration.toml — per-device measured calibration (VER-8 addendum)
# Every value below is MEASURED on the physical station, never copied
# from sim nominals. Conventions are VER-8 v1 (OpenCV): pixel-center
# origin, +Z optical axis, TC-1 xyzw quaternions, meters, VERTICAL fov.

[overhead]
resolution = [640, 480]          # capture resolution (w, h)
fov_deg = 55.0                   # measured VERTICAL field of view
pos = [0.0, 0.0, 0.9]            # camera position, robot-base frame (m)
lookat = [0.45, 0.0, 0.1]        # measured aim point, robot-base frame (m)

[wrist]
resolution = [320, 240]
fov_deg = 70.0
offset_m = [0.0, 0.0, 0.05]      # camera origin in the EE frame (m)
# camera->EE mount rotation, GL convention, TC-1 xyzw — measured, and
# the optical axis MUST point along the EE approach (+Z), issue #109
mount_rotation_gl_xyzw = [0.0, 0.0, 0.0, 1.0]
"""


def build_block(artifact: Path) -> dict:
    from aisle.nodes.ik_trajectory import quat_to_rotation
    from aisle.verifier.calibration import build_calibration_v1

    doc = tomllib.loads(artifact.read_text())
    over, wrist = doc["overhead"], doc["wrist"]
    # the artifact carries the mount as a MEASURABLE TC-1 quaternion;
    # the builder wants the GL rotation matrix
    mount_gl = quat_to_rotation(wrist["mount_rotation_gl_xyzw"])
    return build_calibration_v1(
        overhead_pos=over["pos"],
        overhead_lookat=over["lookat"],
        overhead_resolution=tuple(over["resolution"]),
        overhead_fov_deg=float(over["fov_deg"]),
        wrist_offset_m=wrist["offset_m"],
        wrist_resolution=tuple(wrist["resolution"]),
        wrist_fov_deg=float(wrist["fov_deg"]),
        wrist_mount_rotation_gl=mount_gl,
    )


def check_block(block: dict, lookat) -> list[str]:
    """Stage-0 parity: the block must satisfy the SAME refusal predicates
    the sim verifier applies, using itself as nominal (a self-consistent
    artifact; drift shows up the moment a REALIZED block differs)."""
    from aisle.verifier.calibration import check_calibration

    # the rotation predicate re-derives the expected rotation from the
    # nominal's lookat (VER-8 roll rule); the artifact's measured lookat
    # rides on the nominal side the same way the sim nominal carries it
    nominal = {**block, "_overhead_lookat": lookat}
    problem = check_calibration(block, nominal, jitter_bound_m=0.0)
    return [problem] if problem else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--artifact", type=Path, default=REPO_ROOT / "env" / "calibration.toml")
    args = parser.parse_args()

    if args.template:
        print(TEMPLATE, end="")
        return 0
    if args.build or args.check:
        if not args.artifact.exists():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"{args.artifact} missing — write it from --template, measured",
                    }
                )
            )
            return 1
        block = build_block(args.artifact)
        if args.check:
            doc = tomllib.loads(args.artifact.read_text())
            problems = check_block(block, doc["overhead"]["lookat"])
            print(json.dumps({"ok": not problems, "problems": problems}))
            return 0 if not problems else 1
        print(json.dumps(block))
        return 0
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
