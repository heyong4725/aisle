"""Guard divergence: how often the executed action differed from the proposed
action (#267).

Every motion command traverses `budget-guard`, which does not merely veto — it
CLAMPS (BG-3). So a recorded run carries two different signals:

    joint_cmd       what the policy PROPOSED
    joint_cmd_safe  what the arm actually EXECUTED

They differ exactly when the proposal was out of bounds, which is exactly the
interesting part of the distribution. Before any VLA fine-tune on
expert-graph demonstrations, one question must be answered: **which of those
is the demonstration label?** Training on proposals teaches intent including
corrections the model never sees, and produces a policy that depends on a
guard being downstream. Training on executed actions gives all-legal labels
that are a mixture of two processes — the policy's output and the guard's
correction — so the model imitates an intervention whose trigger it cannot
observe.

The statistic that informs that choice has always been on the wire: the guard
publishes a `violation` record per clamped joint, and the recorder writes it
to Arrow. Nothing aggregated it, so nobody could answer the question. This
module does the aggregation.

Pure functions over decoded rows (CON-12: unit-testable without sim or dora).
"""

from __future__ import annotations

import json
from pathlib import Path

# reasons the guard emits with no numeric delta: the command was replaced
# wholesale by the last safe value rather than moved to a nearest legal one
_NO_DELTA_REASONS = ("wall_timeout", "malformed")


def divergence_summary(violations: list, commands: int) -> dict:
    """Summarize how often, and by how much, the guard changed a command.

    `violations` is one entry PER COMMAND: the list of violation records that
    command produced (empty or absent for clean commands). `commands` is the
    total number of commands the guard processed.

    The rate is per COMMAND, never per record — one command can clamp several
    joints at once, and the label question is about commands. Records are
    still reported, because a run clamping many joints per command is a
    different situation from one clamping a single joint often.

    Magnitude is reported alongside the rate because the rate alone can
    mislead: clamping 1% of commands by 2 rad and clamping 40% by 1e-4 are
    opposite conclusions for the label question. Records carrying no numbers
    are excluded from the magnitude rather than coerced to 0.0, which would
    drag the mean toward zero and understate the problem.

    Degrades rather than raises: this runs over recorded evidence, and a
    truncated recorder tail must not cost the whole summary.
    """
    clamped = 0
    records = 0
    skipped = 0
    no_delta = 0
    by_reason: dict[str, int] = {}
    deltas: list[float] = []

    for entry in violations:
        if not isinstance(entry, list):
            skipped += 1
            continue
        usable = [v for v in entry if isinstance(v, dict) and isinstance(v.get("reason"), str)]
        if not usable:
            skipped += 1
            continue
        clamped += 1
        records += len(usable)
        for v in usable:
            reason = v["reason"]
            by_reason[reason] = by_reason.get(reason, 0) + 1
            requested, clamped_to = v.get("requested"), v.get("clamped")
            if reason in _NO_DELTA_REASONS or requested is None or clamped_to is None:
                no_delta += 1
                continue
            try:
                deltas.append(abs(float(requested) - float(clamped_to)))
            except (TypeError, ValueError):
                no_delta += 1

    rate = round(clamped / commands, 6) if commands > 0 else None
    return {
        "commands": commands,
        "clamped_commands": clamped,
        "violation_records": records,
        "divergence_rate": rate,
        # the headline for #267: when the guard never clamps, the two
        # candidate labels are the same signal and the question is moot
        "labels_coincide": (clamped == 0) if commands > 0 else None,
        "by_reason": by_reason,
        "max_abs_delta": round(max(deltas), 6) if deltas else None,
        "mean_abs_delta": round(sum(deltas) / len(deltas), 6) if deltas else None,
        "records_without_delta": no_delta,
        "skipped_rows": skipped,
    }


def _decode(text) -> list | None:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # a single record, or a wrapper carrying them
        inner = payload.get("violations")
        return inner if isinstance(inner, list) else [payload]
    return None


def summary_for_run(run_dir: Path, node: str | None = None) -> dict:
    """`divergence_summary` for one recorded run directory.

    Reads the guard's `violation` endpoint and the executed-command endpoint
    from the run's Arrow traces. A run with no violation trace is a run where
    the guard never published one — zero clamps, not missing evidence — so it
    reports 0 rather than refusing.
    """
    from aisle.harness.traces import _load  # local: keeps pyarrow off the unit path

    try:
        commands = _load(run_dir, "joint_cmd_safe", node).num_rows
    except (FileNotFoundError, OSError):
        return {**divergence_summary([], 0), "run_dir": str(run_dir), "error": "no command trace"}

    rows: list = []
    try:
        table = _load(run_dir, "violation", node)
    except (FileNotFoundError, OSError):
        table = None
    if table is not None:
        column = "text" if "text" in table.column_names else table.column_names[-1]
        for cell in table.column(column).to_pylist():
            decoded = _decode(cell)
            rows.append(decoded if decoded is not None else "malformed")

    return {**divergence_summary(rows, commands), "run_dir": str(run_dir)}
