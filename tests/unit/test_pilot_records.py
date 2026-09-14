"""Pilot assignment ledger and records producer.

TRT-8, CSE-7, STA-3, STA-11, CSE-8, CSE-13, CSE-14, CON-8.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from aisle.harness import benchmark_statistics as bs
from aisle.harness import pilot_records as pr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import pilot_assignments  # noqa: E402
import pilot_records as records_tool  # noqa: E402

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "analysis/freeze/cse-causal-study-pilot-v1/protocol.json"
SEED = "ab" * 32


def _ledger(tmp_path, reveals=12):
    tmp_path.mkdir(parents=True, exist_ok=True)
    seed = tmp_path / "seed.hex"
    seed.write_text(SEED + "\n")
    ledger = tmp_path / "assignments.json"
    args = ["--campaign", "cse-causal-study-pilot-v1", "--seed", str(seed), "--ledger", str(ledger)]
    assert pilot_assignments.main(["create", *args, "--protocol", str(PROTOCOL)]) == 0
    for _ in range(reveals):
        assert pilot_assignments.main(["reveal", *args]) == 0
    return json.loads(ledger.read_text()), args


def test_ledger_follows_the_protocol_and_is_deterministic(tmp_path, capsys):
    """TRT-8 / CSE-7 / CON-5: arms and count come from the frozen protocol, the same
    private seed reproduces the same balanced order, reveals are sequential and stop
    at the plan's size, and the seed is never written."""
    first, args = _ledger(tmp_path)
    second, _ = _ledger(tmp_path / "again")
    arms = [e["assignment"]["arm"] for e in first["assignments"]]
    assert arms == [e["assignment"]["arm"] for e in second["assignments"]]
    assert (
        arms.count("typed") == 6
        and arms.count("monolithic") == 6
        and first["sessions_per_arm"] == 6
    )
    blocks = {}
    for e in first["assignments"]:
        blocks.setdefault(e["assignment"]["temporal_block"], set()).add(e["assignment"]["arm"])
    assert all(v == {"typed", "monolithic"} for v in blocks.values()) and len(blocks) == 6
    assert (
        SEED not in json.dumps(first)
        and first["protocol_id"] == "cse-causal-study-pilot-v1-protocol-r0"
    )
    assert pilot_assignments.main(["reveal", *args]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["ok"] is False


def _session(
    root,
    entry,
    *,
    success=True,
    tokens=1234,
    checks=2,
    error=None,
    process=True,
    heldout_ok=True,
    finalized=True,
):
    session = root / entry["session_id"]
    session.mkdir(parents=True)
    if not finalized:
        return session
    graph_hash = hashlib.sha256(b"nodes: []\n").hexdigest()
    record = {
        "session_id": entry["session_id"],
        "arm": entry["assignment"]["arm"],
        "plan_id": "sha256:" + "p" * 64,
        "process": {"rc": 0} if process else None,
        "events": [{"kind": "launch"}],
        "classification": "engineering_execution" if error is None else "infrastructure_exclusion",
        "error": error,
        "snapshots": {"final": {"graphs/expert_t1.yaml": {"sha256": graph_hash}}},
        "common_evidence": {
            "budgets": {
                "observed": {"tokens": tokens, "wall_s": 100.5, "controller_reserved_runs": 3}
            },
            "exclusions": [],
        },
    }
    (session / "matched-session.json").write_text(json.dumps(record))
    for index in range(checks):
        (session / f"tool-{index + 1:06d}").mkdir()
        (session / f"tool-{index + 1:06d}/attempt.json").write_text(
            json.dumps({"operation": "check", "process": {"rc": 0}})
        )
    (session / "tool-000098").mkdir()
    (session / "tool-000098/attempt.json").write_text(
        json.dumps({"operation": "check", "process": None})
    )
    (session / "tool-000099").mkdir()
    (session / "tool-000099/attempt.json").write_text(
        json.dumps({"operation": "run", "process": {"rc": 0}})
    )
    if success is not None:
        (session / "heldout").mkdir()
        (session / "heldout/heldout-evidence.json").write_text(
            json.dumps(
                {
                    "schema_version": "aisle.heldout-evidence.v1",
                    "ok": heldout_ok,
                    "session_id": entry["session_id"],
                    "arm": entry["assignment"]["arm"],
                    "plan_id": "sha256:" + "p" * 64,
                    "final_snapshot_sha256": {"graphs/expert_t1.yaml": graph_hash},
                    "session_success": success,
                    "oracle_successes": 30 if success else 3,
                    "n_seeds": 32,
                    "exposure": {
                        "commands": 480,
                        "safety_events": 0,
                        "unit": "command",
                        "error": None,
                    },
                    "launch": {
                        "ok": heldout_ok,
                        "error": None if heldout_ok else "sim backend unavailable",
                    },
                }
            )
        )
    return session


def test_records_rows_validate_and_analyze_under_the_pilot_protocol(tmp_path):
    """STA-3 / STA-11 / CSE-8 / CSE-14: every ledger assignment becomes one row; rows pass
    the analyzer end to end; outcomes come only from held-out evidence; the lifecycle
    follows the executor's classification with its retained reason; exposure is derived."""
    ledger, _ = _ledger(tmp_path, reveals=6)
    protocol = json.loads(PROTOCOL.read_text())
    sessions = tmp_path / "sessions"
    entries = ledger["assignments"]
    _session(sessions, entries[0], success=True)
    _session(sessions, entries[1], success=False, tokens=None, checks=0)
    _session(sessions, entries[2], success=None, error="final tokens budget reached or exceeded")
    _session(sessions, entries[3], success=None, error="launcher failed: crash", process=False)
    _session(sessions, entries[4], success=None, finalized=False)
    records = pr.build_records(
        protocol, ledger, sessions, agent_system="codex-cli", task="t1-l2-realistic"
    )
    bs._validate_records(protocol, records)
    analysis = bs.analyze_campaign(protocol, records)
    assert analysis["ok"] is True
    rows = {r["session_id"]: r for r in records["sessions"]}
    assert len(rows) == 6
    assert rows[entries[0]["session_id"]]["outcome"]["session_success"] is True
    assert rows[entries[0]["session_id"]]["costs"] == {
        "tokens": 1234,
        "wall_s": 100.5,
        "rollouts": 3,
        "validate_fix_cycles": 2,
    }
    assert rows[entries[0]["session_id"]]["exposure"] == {
        "safety_events": 0,
        "commands": 480,
        "unit": "command",
    }
    assert rows[entries[1]["session_id"]]["costs"]["tokens"] is None
    assert rows[entries[1]["session_id"]]["outcome"]["session_success"] is False
    excluded = rows[entries[2]["session_id"]]
    assert excluded["lifecycle_status"] == "infrastructure_excluded"
    assert "budget" in excluded["inclusion"]["reason"] and excluded["budget"]["censored"] is False
    assert rows[entries[3]["session_id"]]["lifecycle_status"] == "infrastructure_excluded"
    assert rows[entries[4]["session_id"]]["lifecycle_status"] == "started"
    assert rows[entries[5]["session_id"]]["lifecycle_status"] == "never_started"


def test_evidence_binding_refuses(tmp_path):
    """CSE-2 / STA-11 / CSE-14: missing or foreign held-out evidence, a refused held-out
    evaluation, an unledgered session directory, and a tampered ledger all refuse or
    exclude instead of yielding a scored row."""
    ledger, _ = _ledger(tmp_path, reveals=3)
    protocol = json.loads(PROTOCOL.read_text())
    sessions = tmp_path / "sessions"
    entries = ledger["assignments"]
    _session(sessions, entries[0], success=None)
    with pytest.raises(pr.RecordsError, match="held-out"):
        pr.build_records(protocol, ledger, sessions, agent_system="a", task="t")
    (sessions / entries[0]["session_id"] / "heldout").mkdir()
    foreign = json.loads(
        (_session(tmp_path / "x", entries[1]) / "heldout/heldout-evidence.json").read_text()
    )
    (sessions / entries[0]["session_id"] / "heldout/heldout-evidence.json").write_text(
        json.dumps(foreign)
    )
    with pytest.raises(pr.RecordsError, match="does not belong"):
        pr.build_records(protocol, ledger, sessions, agent_system="a", task="t")
    import shutil

    shutil.rmtree(sessions)
    _session(sessions, entries[0], success=True, heldout_ok=False)
    row = pr.build_records(protocol, ledger, sessions, agent_system="a", task="t")["sessions"][0]
    assert (
        row["lifecycle_status"] == "infrastructure_excluded"
        and "refused" in row["inclusion"]["reason"]
    )
    (sessions / "stray-attempt2").mkdir()
    with pytest.raises(pr.RecordsError, match="outside the ledger"):
        pr.build_records(protocol, ledger, sessions, agent_system="a", task="t")
    (sessions / "stray-attempt2").rmdir()
    tampered = json.loads(json.dumps(ledger))
    tampered["assignments"][1]["assignment"]["arm"] = (
        "typed" if tampered["assignments"][1]["assignment"]["arm"] == "monolithic" else "monolithic"
    )
    with pytest.raises(pr.RecordsError, match="identity differs"):
        pr.build_records(protocol, tampered, sessions, agent_system="a", task="t")


def test_records_cli_follows_con8(tmp_path, capsys):
    """CON-8: JSON to stdout, exit 0 iff the records were produced; a non-object record
    is a refusal, not a traceback."""
    ledger, args = _ledger(tmp_path, reveals=1)
    sessions = tmp_path / "sessions"
    _session(sessions, ledger["assignments"][0], success=True)
    out = tmp_path / "records.json"
    rc = records_tool.main(
        [
            "--protocol",
            str(PROTOCOL),
            "--ledger",
            args[5],
            "--sessions",
            str(sessions),
            "--agent-system",
            "codex-cli",
            "--task",
            "t",
            "--output",
            str(out),
        ]
    )
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rc == 0 and printed["ok"] is True and printed["sessions"] == 1
    assert json.loads(out.read_text())["schema_version"] == "aisle.stats.records.v1"
    (sessions / ledger["assignments"][0]["session_id"] / "matched-session.json").write_text("[]")
    rc = records_tool.main(
        [
            "--protocol",
            str(PROTOCOL),
            "--ledger",
            args[5],
            "--sessions",
            str(sessions),
            "--agent-system",
            "codex-cli",
            "--task",
            "t",
        ]
    )
    refused = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rc == 1 and refused["ok"] is False
