"""MON-8/MON-12/MON-13: collection cannot outlive its admitted tool budget."""

import json
import os
import time

import pytest

pytestmark = pytest.mark.unit


def _source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "collection-test",
                "seeds": [7],
                "tier": "T1",
                "verifier": "oracle",
            }
        )
    )
    (source / "episodes.jsonl").write_text('{"seed":7,"status":"success"}\n')
    return source


def test_actual_collection_returns_retained_evidence_and_process_receipt(tmp_path):
    """MON-12: normal collection retains actual inputs and a successful child receipt."""
    from aisle.harness.matched_collection import retain_run_bounded

    source = _source(tmp_path)
    destination = tmp_path / "run"
    result = retain_run_bounded(source, destination, run_id="collection-test", timeout_s=10)
    assert result["ok"], result
    assert result["episodes"] == [{"seed": 7, "status": "success"}]
    assert (destination / "raw/manifest.json").read_bytes() == (
        source / "manifest.json"
    ).read_bytes()
    receipt = json.loads((tmp_path / "run-collector/process.json").read_text())
    assert receipt["ok"]
    assert receipt["process"]["rc"] == 0
    assert not receipt["process"]["timed_out"]


def test_receipt_boundary_cannot_publish_success_after_deadline(tmp_path, monkeypatch):
    """MON-8/MON-12: a completed report is invalid if final elapsed accounting is late."""
    from types import SimpleNamespace

    from aisle.harness import matched_collection

    readings = iter([0, 0, 0, 0, 0, 11])
    monkeypatch.setattr(
        matched_collection, "time", SimpleNamespace(monotonic=lambda: next(readings))
    )
    with pytest.raises(ValueError, match="collection wall deadline"):
        matched_collection.retain_run_bounded(
            _source(tmp_path), tmp_path / "run", run_id="collection-test", timeout_s=10
        )
    receipt = json.loads((tmp_path / "run-collector/process.json").read_text())
    assert receipt["wall_s"] > receipt["timeout_s"]
    assert receipt["ok"] is False
    assert receipt["error"] == "collection wall deadline exhausted"
    assert receipt["process"] == {"rc": 0, "timed_out": False}
    assert (tmp_path / "run/run-evidence.json").is_file()


@pytest.mark.parametrize("block", ["sleep", "fifo"])
def test_blocked_collection_is_stopped_reaped_and_partial_evidence_survives(
    tmp_path, monkeypatch, block
):
    """MON-8/MON-12: both Python delay and blocking OS input have a supervised deadline."""
    from aisle.harness import matched_collection

    code = (
        "import os, sys, time; from pathlib import Path; "
        "p=Path(sys.argv[3]); p.mkdir(); "
        "(p/'partial.txt').write_text('retained before blocking'); "
        "(p/'pid').write_text(str(os.getpid())); "
    )
    code += (
        "time.sleep(30)"
        if block == "sleep"
        else "os.mkfifo(p/'blocked'); (p/'blocked').open('rb').read()"
    )
    monkeypatch.setattr(matched_collection, "_BOOTSTRAP", code)
    destination = tmp_path / "run"
    started = time.monotonic()
    with pytest.raises(ValueError, match="collection.*deadline"):
        matched_collection.retain_run_bounded(
            _source(tmp_path), destination, run_id="collection-test", timeout_s=0.5
        )
    assert time.monotonic() - started < 7  # includes bounded process cleanup
    assert (destination / "partial.txt").read_text() == "retained before blocking"
    pid = int((destination / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    receipt = json.loads((tmp_path / "run-collector/process.json").read_text())
    assert not receipt["ok"]
    assert receipt["process"]["timed_out"]
    assert receipt["cleanup"]["remaining"] == []


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_exhausted_or_invalid_budget_cannot_start_collection(tmp_path, monkeypatch, timeout):
    """MON-8: absent remaining time is a refusal before collector dispatch."""
    from aisle.harness import matched_collection

    monkeypatch.setattr(
        matched_collection,
        "spawn_isolated_process",
        lambda *a, **kw: pytest.fail("collector launched without a usable deadline"),
    )
    with pytest.raises(ValueError):
        matched_collection.retain_run_bounded(
            _source(tmp_path), tmp_path / "run", run_id="collection-test", timeout_s=timeout
        )


@pytest.mark.parametrize("fault", ["crash", "missing", "malformed", "fifo_report"])
def test_failed_collector_cannot_return_success(tmp_path, monkeypatch, fault):
    """MON-12: child failure and unsafe result files retain an invalid process receipt."""
    from aisle.harness import matched_collection

    action = {
        "crash": "raise SystemExit(9)",
        "missing": "pass",
        "malformed": "(p/'run-evidence.json').write_text('[]')",
        "fifo_report": "os.mkfifo(p/'run-evidence.json')",
    }[fault]
    monkeypatch.setattr(
        matched_collection,
        "_BOOTSTRAP",
        "import os,sys; from pathlib import Path; p=Path(sys.argv[3]); p.mkdir(); " + action,
    )
    started = time.monotonic()
    with pytest.raises((ValueError, OSError)):
        matched_collection.retain_run_bounded(
            _source(tmp_path), tmp_path / "run", run_id="collection-test", timeout_s=10
        )
    assert time.monotonic() - started < 10
    receipt = json.loads((tmp_path / "run-collector/process.json").read_text())
    assert receipt["ok"] is False
    assert receipt["error"]
    assert receipt["process"]["rc"] == (9 if fault == "crash" else 0)


@pytest.mark.parametrize("failure", ["timeout", "cancel", "service_timeout"])
def test_controller_deadline_retains_invalid_attempt_and_auditable_partial_collection(
    tmp_path, monkeypatch, failure
):
    """MON-8/MON-12: the actual tool controller applies its remainder to collection."""
    import copy
    import subprocess
    import sys

    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness import matched_collection, matched_tools
    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_session import admit_pair

    controller, views, output = _controller(tmp_path, "typed", _development_protocol())
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
        candidate["budget"]["tool_wall_ceiling_s"] = 2
    controller.plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        development=controller.plan["development"],
    )

    def spawn(command, **kwargs):
        run_id = command[command.index("--run-id") + 1]
        source = controller.root / "runs" / run_id
        source.mkdir(parents=True)
        (source / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    **{
                        key: controller.plan["development"][key]
                        for key in ("seeds", "tier", "verifier")
                    },
                }
            )
        )
        (source / "episodes.jsonl").write_text('{"seed":7,"status":"success"}\n')
        return subprocess.Popen(
            [sys.executable, "-c", "print('{\"ok\":true}')"],
            stdin=subprocess.DEVNULL,
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            start_new_session=True,
        )

    monkeypatch.setattr(matched_tools, "spawn_isolated_process", spawn)
    monkeypatch.setattr(
        matched_collection,
        "_BOOTSTRAP",
        (
            "import sys,time; from pathlib import Path; p=Path(sys.argv[3]); "
            "p.mkdir(); (p/'partial.txt').write_text('partial'); time.sleep(30)"
        ),
    )
    if failure == "cancel":
        actual_spawn = matched_collection.spawn_isolated_process

        def interrupted_spawn(*args, **kwargs):
            child = actual_spawn(*args, **kwargs)
            actual_wait = child.wait

            def interrupted_wait(timeout):
                child.wait = actual_wait
                deadline = time.monotonic() + 1
                while not (output / "tool-000001/run/partial.txt").exists():
                    if time.monotonic() >= deadline:
                        raise AssertionError("collector did not reach fixture boundary")
                    time.sleep(0.01)
                raise KeyboardInterrupt

            child.wait = interrupted_wait
            return child

        monkeypatch.setattr(matched_collection, "spawn_isolated_process", interrupted_spawn)
    started = time.monotonic()
    if failure == "cancel":
        with pytest.raises(KeyboardInterrupt):
            controller.run()
        result = json.loads((output / "tool-000001/attempt.json").read_text())
    elif failure == "service_timeout":
        from pathlib import Path

        from aisle.harness.matched_tool_service import ToolService, request_run

        home = Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"])
        channel = home / "tool-channel"
        channel.mkdir()
        with ToolService(controller) as service:
            response = request_run(channel, timeout_s=8)
        assert service.report["ok"], service.report
        assert service.report["processed_requests"] == 1
        assert not service.thread.is_alive()
        result = json.loads((output / "tool-000001/attempt.json").read_text())
        for key in ("ok", "classification", "result", "error", "attempt"):
            assert response[key] == result[key]
    else:
        result = controller.run()
    assert time.monotonic() - started < 10
    assert not result["ok"], result
    assert result["classification"] == "infrastructure_exclusion"
    assert ("KeyboardInterrupt" if failure == "cancel" else "collection wall deadline") in result[
        "error"
    ]
    assert result["process"]["rc"] == 0
    assert result["run_evidence"] is None
    assert (output / "tool-000001/run/partial.txt").read_text() == "partial"
    assert json.loads((output / "tool-000001/attempt.json").read_text()) == result
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm="typed",
        development=controller.plan["development"],
    )
    assert audit["ok"], audit
    assert audit["reserved_runs"] == 1
    if failure == "service_timeout":
        assert audit["service_verified"]

    # Rehashing all copies must not make malformed receipts acceptable.
    import hashlib

    receipt_path = output / "tool-000001/run-collector/process.json"
    receipt = json.loads(receipt_path.read_text())
    journal_path = output / "tool-events.jsonl"
    events = [json.loads(line) for line in journal_path.read_text().splitlines()]
    for malformed in ([], {**receipt, "error": ["invented"]}):
        raw = json.dumps(malformed).encode()
        receipt_path.write_bytes(raw)
        altered = copy.deepcopy(result)
        altered["artifacts"]["run-collector/process.json"] = hashlib.sha256(raw).hexdigest()
        altered.pop("immutable_id")
        altered["immutable_id"] = matched_tools._digest(altered)
        (output / "tool-000001/attempt.json").write_text(json.dumps(altered))
        changed_events = copy.deepcopy(events)
        for event in changed_events:
            if event.get("record") == result:
                event["record"] = altered
        journal_path.write_text("".join(json.dumps(event) + "\n" for event in changed_events))
        rejected = audit_tool_journal(
            output,
            session_id=controller.session_id,
            plan_id=controller.plan["immutable_id"],
            arm="typed",
            development=controller.plan["development"],
        )
        assert not rejected["ok"], rejected
        assert rejected["error"]
        assert "collection" in rejected["error"]
