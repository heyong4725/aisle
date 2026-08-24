"""t2-read-ladder (agent-authored, campaign t2_breakthrough agent 2):
ik-trajectory with a FAR-FIRST read ladder.

Evidence (offline trace audit of the arm-L T2 runs, campaign-holdout-L-T2
r1 + fresh-seed runs): every collision was TOUR-phase, at the read-park
approach/advance of the candidate being read — the TCP ends centimeters
from the knocked box, because the stock ladder prefers 0.13 m camera
range, which parks the fingertips ~7.7 cm (nominal, less pose error and
box half-depth) from the face. Offline IK audit: flat entries at
0.20/0.24 m solve AND stage for the same faces at the same rate as
0.13 m, with TCP-to-face clearance 14.7/18.7 cm; far PITCHED entries
rarely solve, so pitched stays at 0.16 m. Recorded far reads (thin, n=9)
match with margins up to 0.78; refusals keep strong absolute scores, and
the tsm's refusal-retry walks to the next rung, ending at the old close
entries as last resorts — read capability is a superset, only the
PREFERENCE moves outward.

Also folds in the v7 lesson at the right layer: near-side faces with low
clearance over their board jam every flat close entry (arm-L run 73d6d1:
56/115 parks bailed), so those faces select the pitched-first ladder —
the same deterministic choice solve_read_poses already makes for
far-side (+y) faces — instead of the tsm reaching into ladder indices.

Everything else (staged backoff approach, tracking tolerance, retreat
via home, grasp pipeline) is aisle.nodes.ik_trajectory unchanged: this
module swaps the ladder tables and the ladder SELECTOR, then runs the
stock main().
"""

from __future__ import annotations

import numpy as np

import aisle.nodes.ik_trajectory as ik

# far-first: collision-safe ranges first, the stock close entries kept as
# last-resort rungs (deterministic, CON-5)
READ_LADDER_FAR_FIRST = (
    (0.20, 0.0, 0.0),
    (0.16, 0.0, 0.35),
    (0.20, 0.25, 0.0),
    (0.20, -0.25, 0.0),
    (0.16, 0.25, 0.35),
    (0.16, -0.25, 0.35),
    (0.24, 0.0, 0.0),
    (0.16, 0.0, 0.0),
    (0.13, 0.0, 0.0),
)
PITCHED_FIRST_LADDER = (
    (0.16, 0.0, 0.35),
    (0.16, 0.25, 0.35),
    (0.16, -0.25, 0.35),
    (0.20, 0.0, 0.35),
    (0.20, 0.0, 0.0),
    (0.20, 0.25, 0.0),
    (0.20, -0.25, 0.0),
    (0.24, 0.0, 0.0),
    (0.16, 0.0, 0.0),
    (0.13, 0.0, 0.0),
)
# near-side faces under this clearance over their board take the pitched
# ladder (v7's measured jam class: flat entries press into the shelf)
PITCH_FIRST_CLEARANCE_M = 0.05


def _board_tops() -> tuple[float, ...]:
    from aisle.nodes.label_reader import shelf_board_tops

    return tuple(shelf_board_tops())


_TOPS: tuple[float, ...] | None = None


def solve_read_poses(face, mount, q0, embodiment: str = "franka"):
    """Stock solve_read_poses with the far-first tables and the low-
    clearance pitched-first selection folded into the ladder choice."""
    global _TOPS
    if embodiment != "franka":
        return []
    face = np.asarray(face, dtype=np.float64)
    pitched_first = face[1] > ik.FAR_SIDE_Y
    if not pitched_first:
        if _TOPS is None:
            _TOPS = _board_tops()
        below = [t for t in _TOPS if t <= face[2] + 0.01]
        board = max(below) if below else min(_TOPS)
        if face[2] - board < PITCH_FIRST_CLEARANCE_M:
            pitched_first = True
    ladder = PITCHED_FIRST_LADDER if pitched_first else READ_LADDER_FAR_FIRST
    solutions = []
    for range_m, azimuth, pitch in ladder:
        tcp, r_flange = ik.read_flange_targets(face, range_m, azimuth, mount, pitch)
        for seed in (q0, *ik._CANONICAL_SEEDS):
            q = ik._ik_once(tcp, r_flange, seed, embodiment)
            if q is not None:
                q_far = None
                for backoff in ik.READ_STAGE_BACKOFFS_M:
                    far_tcp, far_rot = ik.read_flange_targets(
                        face, range_m + backoff, azimuth, mount, pitch
                    )
                    for far_seed in (q, *ik._CANONICAL_SEEDS):
                        q_far = ik._ik_once(far_tcp, far_rot, far_seed, embodiment)
                        if q_far is not None:
                            break
                    if q_far is not None:
                        break
                if q_far is not None:
                    solutions.append((q, range_m, pitch, q_far))
                break
    return solutions


def main() -> None:  # pragma: no cover — dora runtime
    ik.solve_read_poses = solve_read_poses
    ik.main()


if __name__ == "__main__":  # pragma: no cover
    main()
