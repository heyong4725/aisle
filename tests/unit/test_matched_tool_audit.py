"""MON-12/MON-13: validate controller journals before counting tool evidence."""

import json

import pytest
from test_matched_tools import _controller

pytestmark = pytest.mark.unit


def _audit(controller, output):
    from aisle.harness.matched_evidence import audit_tool_journal

    return audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
    )


def test_tool_audit_binds_attempt_files_and_counts_observed_processes(tmp_path):
    """MON-12: attempted and executed tool counts remain distinct after budget refusal."""
    controller, _, output = _controller(tmp_path, "monolithic")
    first = controller.check()
    controller.check()
    report = _audit(controller, output)
    assert report["ok"] is True, report
    assert report["attempted_tools"] == 2
    assert report["executed_tools"] == 1
    assert report["wall_s"] >= first["wall_s"]
    assert report["files"]["tool-000001/stdout.json"]
    assert report["files"]["tool-000001/attempt.json"]
    assert report["exclusions"]


@pytest.mark.parametrize(
    "fault", ["missing_finish", "wrong_session", "changed_stream", "changed_record", "symlink"]
)
def test_invalid_tool_journal_cannot_supply_complete_metrics(tmp_path, fault):
    """MON-13: partial, cross-session or altered tool evidence fails its audit."""
    controller, _, output = _controller(tmp_path, "monolithic")
    controller.check()
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    if fault == "missing_finish":
        journal.write_text(json.dumps(events[0]) + "\n")
    elif fault == "wrong_session":
        events[0]["session_id"] = "other-session"
        journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    elif fault == "changed_stream":
        (output / "tool-000001/stdout.json").write_text('{"ok":false}')
    elif fault == "changed_record":
        (output / "tool-000001/attempt.json").write_text("{}")
    else:
        stream = output / "tool-000001/stdout.json"
        stream.unlink()
        stream.symlink_to(output / "tool-events.jsonl")
    report = _audit(controller, output)
    assert report["ok"] is False
    assert report["error"]


def test_session_finalization_rejects_a_damaged_tool_journal(tmp_path):
    """MON-12/MON-13: a zero process exit cannot mask invalid nested tool evidence."""
    from aisle.harness.matched_session import execute_session
    from aisle.harness.matched_tools import ToolController

    controller, views, _ = _controller(tmp_path, "monolithic")
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps(
            {
                "schema_version": "aisle.hidden-access-log.v1",
                "adapter_active": True,
                "complete": True,
                "events": [],
            }
        )
    )

    def launch(output):
        active = ToolController(
            controller.plan,
            controller.root,
            views,
            controller.arm,
            output,
            session_id="damaged-journal",
            python=controller.python,
            profile_path=controller.profile_path,
            attestation=controller.attestation,
        )
        active.check()
        journal = output / "tool-events.jsonl"
        journal.write_text(journal.read_text().splitlines()[0] + "\n")
        return {"rc": 0, "tokens": 0, "wall_s": 1.0}

    result = execute_session(
        controller.plan,
        controller.root,
        views,
        controller.arm,
        tmp_path / "session",
        session_id="damaged-journal",
        launch=launch,
        hidden_access_log=access,
    )
    assert result["ok"] is False
    assert result["tool_audit"]["ok"] is False
    assert result["common_evidence"]["budgets"]["observed"]["tool_calls"] is None
    assert "tool journal" in result["error"]


def test_self_consistent_record_cannot_omit_required_process_streams(tmp_path):
    """MON-12: matching hashes do not excuse missing raw evidence for an observed process."""
    from aisle.harness.matched_session import _digest

    controller, _, output = _controller(tmp_path, "monolithic")
    record = controller.check()
    del record["artifacts"]["stdout.json"]
    record.pop("immutable_id")
    record["immutable_id"] = _digest(record)
    (output / "tool-000001/stdout.json").unlink()
    (output / "tool-000001/attempt.json").write_text(json.dumps(record))
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["record"] = record
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = _audit(controller, output)
    assert report["ok"] is False
    assert "required" in report["error"]


def test_tool_verdict_must_match_retained_stdout(tmp_path):
    """MON-12: a rehashed summary cannot replace the tool's retained output verdict."""
    from aisle.harness.matched_session import _digest

    controller, _, output = _controller(tmp_path, "monolithic")
    record = controller.check()
    assert record["result"]["ok"] is True
    record["result"]["ok"] = False
    record["ok"] = False
    record["process"]["rc"] = 1
    record.pop("immutable_id")
    record["immutable_id"] = _digest(record)
    (output / "tool-000001/attempt.json").write_text(json.dumps(record))
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["record"] = record
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = _audit(controller, output)
    assert report["ok"] is False
    assert "verdict" in report["error"]


@pytest.mark.parametrize("field", ["development_id", "run_id"])
def test_observed_run_requires_protocol_and_attempt_identity(tmp_path, field):
    """MON-12/MON-13: rehashed run records cannot lose their admitted identity."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_session import _digest

    development = _development_protocol()
    controller, views, output = _controller(tmp_path, "monolithic", development=development)
    (views["monolithic"] / "experts/monolithic/expert_t1.py").write_text("invalid python !!!")
    record = controller.run()
    assert record["process"] is not None
    record[field] = None
    record.pop("immutable_id")
    record["immutable_id"] = _digest(record)
    (output / "tool-000001/attempt.json").write_text(json.dumps(record))
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["record"] = record
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
        development=development,
    )
    assert report["ok"] is False
    assert "identity" in report["error"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "changed_raw",
        "missing_report",
        "extra_raw",
        "missing_evaluator",
        "false_outcome",
        "false_evaluator",
        "false_guards",
        "false_simulator_work",
    ],
)
def test_run_audit_covers_nested_retained_files(tmp_path, fault):
    """MON-12/MON-13: session audit hashes every retained run file and rejects drift."""
    from test_matched_run_evidence import _run
    from test_matched_session import _development_protocol

    from aisle.harness.matched_evidence import audit_tool_journal, retain_run
    from aisle.harness.matched_session import _digest

    development = _development_protocol()
    controller, views, output = _controller(tmp_path, "monolithic", development=development)
    (views["monolithic"] / "experts/monolithic/expert_t1.py").write_text("invalid python !!!")
    record = controller.run()
    source = _run(tmp_path)
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["run_id"] = record["run_id"]
    (source / "manifest.json").write_text(json.dumps(manifest))
    retained = output / "tool-000001/run"
    record["run_evidence"] = retain_run(source, retained, run_id=record["run_id"])
    if fault == "missing_evaluator":
        record["run_evidence"].pop("evaluator")
    elif fault == "false_outcome":
        record["run_evidence"]["episodes"][0]["success"] = True
    elif fault == "false_evaluator":
        record["run_evidence"]["evaluator"]["complete"] = False
    elif fault == "false_guards":
        record["run_evidence"]["guards"] = {"status": "retained", "summary": {"safe": True}}
    elif fault == "false_simulator_work":
        record["run_evidence"]["simulator_work"] = {
            "status": "recomputed",
            "summary": {"completed_env_sim_ns": 999999},
            "error": None,
        }
    if fault in {
        "missing_evaluator",
        "false_outcome",
        "false_evaluator",
        "false_guards",
        "false_simulator_work",
    }:
        (retained / "run-evidence.json").write_text(json.dumps(record["run_evidence"]))
    record.pop("immutable_id")
    record["immutable_id"] = _digest(record)
    (output / "tool-000001/attempt.json").write_text(json.dumps(record))
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["record"] = record
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    if fault == "changed_raw":
        (retained / "raw/traces/raw.bin").write_bytes(b"changed")
    elif fault == "missing_report":
        (retained / "run-evidence.json").unlink()
    elif fault == "extra_raw":
        (retained / "raw/extra.txt").write_text("unindexed")
    report = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
        development=development,
    )
    if fault is None:
        assert report["ok"] is True, report
        assert report["files"]["tool-000001/run/raw/traces/raw.bin"]
        assert report["files"]["tool-000001/run/run-evidence.json"]
        assert report["runs"][0]["collection"] == record["run_evidence"]
        assert report["runs"][0]["collection_path"] == "tool-000001/run/run-evidence.json"
    else:
        assert report["ok"] is False
        assert report["error"]


def test_common_envelope_exposes_audited_run_attempt_and_partial_collection(tmp_path):
    """MON-12/MON-13: run attempts reach the common envelope without claiming missing evidence."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_evidence import audit_tool_journal, common_envelope

    development = _development_protocol()
    controller, views, output = _controller(tmp_path, "monolithic", development=development)
    (views["monolithic"] / "experts/monolithic/expert_t1.py").write_text("invalid python !!!")
    attempt = controller.run()
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
        development=development,
    )
    assert audit["ok"] is True, audit
    record = {
        "session_id": controller.session_id,
        "plan_id": controller.plan["immutable_id"],
        "arm": controller.arm,
        "artifacts": audit["files"],
        "process": None,
        "tool_audit": audit,
        "lifecycle": {},
        "snapshots": {},
        "events": [],
        "postflight": None,
        "error": None,
    }
    common = common_envelope(record, controller.plan["arms"][controller.arm], None)
    assert common["runs"]["status"] == "retained"
    assert common["runs"]["entries"][0]["run_id"] == attempt["run_id"]
    assert common["runs"]["entries"][0]["attempt_id"] == attempt["immutable_id"]
    assert common["runs"]["entries"][0]["collection"] is None
    run = common["runs"]["entries"][0]
    assert run["ok"] is False
    assert run["result"] == attempt["result"]
    assert run["process"] == attempt["process"]
    assert run["result_path"] == "tool-000001/stdout.json"
    assert common["evaluator"]["status"] == "not_collected"
    assert common["guards"]["status"] == "not_collected"
    assert common["complete"] is False
    audit["ok"] = False
    rejected = common_envelope(record, None, None)
    assert rejected["runs"]["status"] == "not_collected"
    assert rejected["runs"]["entries"] == []


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_session_finalization_exposes_child_run_evaluator_and_guards(tmp_path, monkeypatch, arm):
    """MON-12/MON-13: actual fixture child files reach both arms' common envelopes.

    The substituted child produces engineering fixtures, not simulation results
    or independent evaluator/confinement evidence.
    """
    import subprocess
    import sys

    from test_matched_session import _development_protocol

    from aisle.harness import matched_tools
    from aisle.harness.matched_session import execute_session
    from aisle.harness.matched_tools import ToolController

    controller, views, _ = _controller(tmp_path, arm, development=_development_protocol())
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps(
            {
                "schema_version": "aisle.hidden-access-log.v1",
                "adapter_active": True,
                "complete": True,
                "events": [],
            }
        )
    )
    code = """
import json, sys
from pathlib import Path
import pyarrow as pa
source = Path(sys.argv[1]); run_id = sys.argv[2]
source.mkdir(parents=True)
(source / "manifest.json").write_text(json.dumps({
    "run_id": run_id, "seeds": [7], "tier": "T1", "verifier": "oracle"}))
(source / "episodes.jsonl").write_text('{"seed":7,"success":false}\\n')
(source / "traces").mkdir()
for topic, table in (
    ("joint_cmd_safe", pa.table({"data": [[0.5], [1.0]]})),
    ("violation", pa.table({"text": ['[{"reason":"joint_limit","requested":2.0,"clamped":1.0}]']})),
):
    with (source / "traces" / f"guard__{topic}.arrow").open("wb") as stream:
        with pa.ipc.new_stream(stream, table.schema) as writer:
            writer.write_table(table)
print('{"ok":false}')
sys.exit(1)
"""

    def spawn(command, **kwargs):
        run_id = command[command.index("--run-id") + 1]
        return subprocess.Popen(
            [sys.executable, "-c", code, str(controller.root / "runs" / run_id), run_id],
            stdin=subprocess.DEVNULL,
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            start_new_session=True,
        )

    monkeypatch.setattr(matched_tools, "spawn_isolated_process", spawn)

    def launch(output):
        active = ToolController(
            controller.plan,
            controller.root,
            views,
            arm,
            output,
            session_id="run-envelope",
            python=controller.python,
            profile_path=controller.profile_path,
            attestation=controller.attestation,
        )
        attempt = active.run()
        assert attempt["classification"] == "tool_result", attempt
        (output / "session.jsonl").write_text('{"type":"fixture"}\n')
        return {"rc": 0, "tokens": 0, "wall_s": 1.0}

    output = tmp_path / "integrated-session"
    result = execute_session(
        controller.plan,
        controller.root,
        views,
        arm,
        output,
        session_id="run-envelope",
        launch=launch,
        hidden_access_log=access,
    )
    assert result["ok"] is True, result
    common = result["common_evidence"]
    run = common["runs"]["entries"][0]
    assert run["collection"]["episodes"][0]["success"] is False
    assert common["evaluator"]["entries"][0]["evidence"]["observed_seeds"] == [7]
    assert common["guards"]["entries"][0]["evidence"]["summary"]["divergence_rate"] == 0.5
    assert common["content_hashes"][run["collection_path"]]
    assert common["complete"] is False
    retained = json.loads((output / "matched-session.json").read_text())
    assert retained["common_evidence"] == common


def test_tool_attempt_lifecycle_is_retained_and_audited(tmp_path):
    """MON-12/MON-13: controller-observed tool lifecycle instants remain ordered."""
    from datetime import datetime

    from aisle.harness.matched_session import _digest

    controller, _, output = _controller(tmp_path, "monolithic")
    instants = iter(["2026-09-07T10:00:00+00:00", "2026-09-07T10:00:02+00:00"])
    controller.now = lambda: next(instants)
    record = controller.check()
    assert record["lifecycle"] == {
        "started_at": "2026-09-07T10:00:00+00:00",
        "finished_at": "2026-09-07T10:00:02+00:00",
    }
    assert datetime.fromisoformat(record["lifecycle"]["started_at"]).utcoffset() is not None
    assert _audit(controller, output)["ok"] is True
    record["lifecycle"]["finished_at"] = "2026-09-07T09:59:00+00:00"
    record.pop("immutable_id")
    record["immutable_id"] = _digest(record)
    (output / "tool-000001/attempt.json").write_text(json.dumps(record))
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["record"] = record
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = _audit(controller, output)
    assert report["ok"] is False
    assert "lifecycle" in report["error"]


def test_run_reservations_are_audited_against_admitted_episode_budget(tmp_path):
    """MON-8/MON-12: a rehashed reservation cannot exceed the admitted episode ceiling."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_session import _digest

    development = _development_protocol()
    controller, views, output = _controller(tmp_path, "monolithic", development=development)
    (views["monolithic"] / "experts/monolithic/expert_t1.py").write_text("invalid python !!!")
    record = controller.run()
    assert record["reservation"] == {"runs": 1, "episodes": 1}
    record["reservation"]["episodes"] = 2
    record.pop("immutable_id")
    record["immutable_id"] = _digest(record)
    (output / "tool-000001/attempt.json").write_text(json.dumps(record))
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["record"] = record
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
        development=development,
    )
    assert audit["ok"] is False
    assert "reservation" in audit["error"]


@pytest.mark.parametrize("limit", ["run_ceiling", "episode_ceiling"])
def test_cumulative_run_reservations_cannot_exceed_protocol(tmp_path, limit):
    """MON-8/MON-12: individually valid reservations must also fit the cumulative budget."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_session import _digest

    development = _development_protocol()
    development.update(run_ceiling=2, episode_ceiling=2)
    controller, views, output = _controller(tmp_path, "monolithic", development=development)
    (views["monolithic"] / "experts/monolithic/expert_t1.py").write_text("invalid python !!!")
    controller.run()
    controller.run()
    development[limit] = 1
    journal = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    for event in events:
        if event["event"] != "finished":
            continue
        record = event["record"]
        record["development_id"] = _digest(development)
        record.pop("immutable_id")
        record["immutable_id"] = _digest(record)
        (output / f"tool-{record['attempt']:06d}/attempt.json").write_text(json.dumps(record))
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
        development=development,
    )
    assert report["ok"] is False
    assert "reservations exceed" in report["error"]
