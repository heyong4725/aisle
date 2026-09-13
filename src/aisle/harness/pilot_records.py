"""Derive `aisle.stats.records.v1` session rows from retained pilot evidence.

STA-3/STA-11: every randomized assignment in the verified ledger becomes exactly
one row, started or not; a row's outcome comes only from the session's retained
held-out evidence (CSE-2), cross-checked against the session record; costs are
the executor's observed budgets, `None` when unobserved (never zero); validate-
fix cycles are the executed `check` tool attempts; exposure is the held-out
run's SPEC 470 ledger counts (STA-10). The lifecycle follows the executor's own
classification: an `engineering_execution` session is a completed, included
session whatever its exit status; anything else keeps the executor's retained
reason as its exclusion (CSE-14). The executor retains no structured budget
stop today, so no row is censored (CSE-13 limitation, named in the docs).
Anything missing fails closed.
"""

from __future__ import annotations

from pathlib import Path

from aisle.harness.heldout_eval import HELDOUT_DIR, HELDOUT_SCHEMA, read_json
from aisle.harness.treatment_randomization import ASSIGNMENT_SCHEMA_VERSION, _content_id

RECORDS_SCHEMA = "aisle.stats.records.v1"
COST_FIELDS = ("tokens", "wall_s", "rollouts", "validate_fix_cycles")
#: ledger arm label -> SPEC 400 treatment arm
TREATMENTS = {"typed": "typed_dataflow", "monolithic": "monolithic"}
NEUTRAL_BUDGET = {"censored": False, "reason": None}


class RecordsError(ValueError):
    """Retained evidence cannot support a records row."""


def _json(path: Path) -> dict:
    return read_json(path, RecordsError)


def check_attempts(session_dir: Path) -> int:
    """Validate-fix cycles: executed controller `check` attempts (a process ran)."""
    count = 0
    for attempt in sorted(Path(session_dir).glob("tool-*/attempt.json")):
        record = _json(attempt)
        if record.get("operation") == "check" and record.get("process") is not None:
            count += 1
    return count


def _verify_assignment(assignment: dict, commitment: dict, index: int) -> None:
    """A revealed assignment must be its own content id and bound to the public
    commitment at the expected index. The identity is the randomization module's
    (`_content_id`); the module itself stays untouched because the retained TRT-8
    capability audit binds its source hash."""
    if (
        type(assignment) is not dict
        or assignment.get("schema_version") != ASSIGNMENT_SCHEMA_VERSION
        or assignment.get("algorithm_sha256") != commitment.get("algorithm_sha256")
        or assignment.get("assignment_index") != index
        or assignment.get("plan_commitment") != commitment.get("plan_commitment")
        or assignment.get("randomization_seed_commitment")
        != commitment.get("randomization_seed_commitment")
        or assignment.get("arm") not in (commitment.get("arms") or [])
        or assignment.get("temporal_block") not in (commitment.get("temporal_blocks") or [])
    ):
        raise RecordsError(f"ledger assignment {index} is not bound to the sealed plan")
    if _content_id(assignment) != assignment.get("immutable_id"):
        raise RecordsError(f"ledger assignment {index} identity differs")


def verify_ledger(ledger: dict) -> list[dict]:
    """The ledger's assignments must be the sealed plan's own records, in order."""
    commitment = ledger.get("commitment")
    entries = ledger.get("assignments")
    if not isinstance(commitment, dict) or not isinstance(entries, list):
        raise RecordsError("ledger is mis-shaped")
    if len(entries) > commitment.get("assignments", 0):
        raise RecordsError("ledger reveals more assignments than the sealed plan holds")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or type(entry.get("session_id")) is not str:
            raise RecordsError(f"ledger assignment {index} has no session id")
        _verify_assignment(entry.get("assignment"), commitment, index)
    return entries


def _unscored_row(base: dict, *, lifecycle_status: str, reason: str) -> dict:
    return {
        **base,
        "lifecycle_status": lifecycle_status,
        "inclusion": {"included": False, "reason": reason},
        "budget": dict(NEUTRAL_BUDGET),
        "outcome": {"session_success": None, "accepted_time_s": None},
        "costs": dict.fromkeys(COST_FIELDS),
        "exposure": {"safety_events": None, "commands": None, "unit": "command"},
        "wall_s": 0.0,
    }


def _heldout_for(session_dir: Path, entry: dict, record: dict) -> dict | None:
    path = Path(session_dir) / HELDOUT_DIR / "heldout-evidence.json"
    if not path.is_file():
        return None
    heldout = _json(path)
    final = {
        name: row["sha256"]
        for name, row in (record.get("snapshots") or {}).get("final", {}).items()
    }
    if (
        heldout.get("schema_version") != HELDOUT_SCHEMA
        or heldout.get("session_id") != entry["session_id"]
        or heldout.get("arm") != entry["assignment"]["arm"]
        or heldout.get("plan_id") != record.get("plan_id")
        or heldout.get("final_snapshot_sha256") != final
    ):
        raise RecordsError(f"held-out evidence does not belong to {entry['session_id']}")
    return heldout


def _scored_row(base: dict, entry: dict, session_dir: Path, record: dict) -> dict:
    session_id = entry["session_id"]
    heldout = _heldout_for(session_dir, entry, record)
    if heldout is None:
        raise RecordsError(f"session lacks held-out evidence: {session_id}")
    if heldout.get("ok") is not True:
        reason = "held-out evaluation refused: " + str((heldout.get("launch") or {}).get("error"))
        return _unscored_row(base, lifecycle_status="infrastructure_excluded", reason=reason)
    observed = ((record.get("common_evidence") or {}).get("budgets") or {}).get("observed") or {}
    wall = observed.get("wall_s")
    if type(wall) not in (int, float):
        raise RecordsError(f"included session has no observed wall time: {session_id}")
    exposure = heldout.get("exposure") or {}
    if not all(type(exposure.get(k)) is int for k in ("commands", "safety_events")):
        raise RecordsError(f"included session has no derived exposure: {session_id}")
    return {
        **base,
        "lifecycle_status": "completed",
        "inclusion": {"included": True, "reason": None},
        "budget": dict(NEUTRAL_BUDGET),
        "outcome": {"session_success": bool(heldout["session_success"]), "accepted_time_s": None},
        "costs": {
            "tokens": observed.get("tokens"),
            "wall_s": wall,
            "rollouts": observed.get("controller_reserved_runs"),
            "validate_fix_cycles": check_attempts(session_dir),
        },
        "exposure": {
            "safety_events": exposure["safety_events"],
            "commands": exposure["commands"],
            "unit": "command",
        },
        "wall_s": float(wall),
        "heldout": {"oracle_successes": heldout["oracle_successes"], "n_seeds": heldout["n_seeds"]},
    }


def session_row(
    protocol: dict, entry: dict, session_dir: Path | None, *, agent_system: str, task: str
) -> dict:
    """One records row for one ledger assignment (started or not)."""
    assignment = entry["assignment"]
    base = {
        "session_id": entry["session_id"],
        "protocol_id": protocol["protocol_id"],
        "campaign_id": protocol["campaign_id"],
        "campaign_phase": protocol["campaign_phase"],
        "treatment": TREATMENTS[assignment["arm"]],
        "agent_system": agent_system,
        "task": task,
        "temporal_block": assignment["temporal_block"],
        "assignment_status": "randomized",
        "artifacts": [],
    }
    if session_dir is None:
        return _unscored_row(
            base,
            lifecycle_status="never_started",
            reason="assignment revealed but no session started",
        )
    if not (session_dir / "matched-session.json").is_file():
        return _unscored_row(
            base, lifecycle_status="started", reason="session directory without a finalized record"
        )
    record = _json(session_dir / "matched-session.json")
    if record.get("session_id") != entry["session_id"] or record.get("arm") != assignment["arm"]:
        raise RecordsError(f"session evidence does not match the ledger: {entry['session_id']}")
    if record.get("classification") != "engineering_execution" or record.get("error") is not None:
        reason = record.get("error") or "executor classified the session as an exclusion"
        return _unscored_row(base, lifecycle_status="infrastructure_excluded", reason=str(reason))
    return _scored_row(base, entry, session_dir, record)


def build_records(
    protocol: dict, ledger: dict, sessions_root: Path, *, agent_system: str, task: str
) -> dict:
    if ledger.get("campaign_id") != protocol["campaign_id"]:
        raise RecordsError("ledger campaign differs from the protocol")
    entries = verify_ledger(ledger)
    root = Path(sessions_root)
    ledgered = {entry["session_id"] for entry in entries}
    if root.is_dir():
        stray = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in ledgered)
        if stray:
            raise RecordsError("session directories outside the ledger: " + ", ".join(stray))
    rows = []
    for entry in entries:
        session_dir = root / entry["session_id"]
        rows.append(
            session_row(
                protocol,
                entry,
                session_dir if session_dir.is_dir() else None,
                agent_system=agent_system,
                task=task,
            )
        )
    return {
        "schema_version": RECORDS_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "campaign_id": protocol["campaign_id"],
        "campaign_phase": protocol["campaign_phase"],
        "ledger_commitment": ledger["commitment"]["plan_commitment"],
        "sessions": rows,
    }
