"""Controller-run held-out evaluation (CSE-2, CSE-9, BND-3, CON-8)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness import heldout_eval as he

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _session(tmp_path, *, intact=True, arm="typed"):
    session = tmp_path / "session"
    (session / "final" / "graphs").mkdir(parents=True)
    graph = session / "final/graphs/expert_t1.yaml"
    graph.write_text("nodes: []\n")
    record = {
        "session_id": "pilot-typed-1",
        "arm": arm,
        "plan_id": "sha256:" + "a" * 64,
        "ok": intact,
        "error": None if intact else "launcher failed",
        "classification": "engineering_execution" if intact else "infrastructure_exclusion",
        "process": {"rc": 0},
        "lifecycle": {"finished_at": "2026-09-13T00:00:00+00:00"},
        "snapshots": {
            "final": {
                "graphs/expert_t1.yaml": {
                    "sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
                    "mode": 0o644,
                }
            }
        },
    }
    (session / "matched-session.json").write_text(json.dumps(record))
    development = {
        "schema_version": "aisle.matched-development.v1",
        "purpose": "expert_parity",
        "tier": "T1",
        "embodiment": "franka",
        "verifier": "oracle",
        "reset": "teleport",
        "seeds": [0, 1],
        "run_ceiling": 1,
        "episode_ceiling": 2,
        "timeout_s": 30,
    }
    (session / "admission.json").write_text(json.dumps({"development": development}))
    return session


def _registered(tmp_path, seeds):
    from aisle.harness.freeze import canonical_bytes

    salt = b"s" * 32
    (tmp_path / "seeds.json").write_text(json.dumps(seeds))
    (tmp_path / "salt.bin").write_bytes(salt)
    commitment = "sha256:" + hashlib.sha256(salt + canonical_bytes(seeds)).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"campaign_id": "pilot-fixture", "seed_commitment": commitment})
    )
    return commitment


def _request(tmp_path, session, seeds=(1001, 1002, 1003), threshold=2):
    _registered(tmp_path, list(seeds))
    return {
        "session_dir": str(session),
        "controller_root": str(ROOT),
        "manifest": str(tmp_path / "manifest.json"),
        "seeds_source": str(tmp_path / "seeds.json"),
        "salt_source": str(tmp_path / "salt.bin"),
        "rule": {"kind": "oracle_successes_at_least", "threshold": threshold},
        "timeout_s": 60,
    }


def _launcher(calls, report=None):
    def launch(arm, fields, view, seeds, run_id, timeout_s, development):
        calls.append(
            {
                "arm": arm,
                "fields": fields,
                "view": Path(view),
                "seeds": list(seeds),
                "run_id": run_id,
            }
        )
        return {"ok": True} if report is None else report

    return launch


def _exposure(run_dir, controller_root, campaign_id):
    return {"commands": 120, "safety_events": 0, "unit": "command", "error": None}


def _verdicts(statuses):
    return lambda run_dir: {
        f"ep-{index:04d}": {"status": status, "failure": None}
        for index, status in enumerate(statuses)
    }


def test_success_follows_the_frozen_rule_over_oracle_verdicts(tmp_path):
    """CSE-2 / BND-3: the submitted system runs on the registered private seeds in a
    rebuilt view of the committed controller tree; the oracle tap scores it by goal id."""
    session = _session(tmp_path)
    request = _request(tmp_path, session)
    calls = []
    evidence = he.run_heldout(
        request,
        launcher=_launcher(calls),
        verdicts=_verdicts(["success", "success", "fail"]),
        exposure=_exposure,
    )
    assert evidence["ok"] is True and evidence["session_success"] is True
    assert evidence["oracle_successes"] == 2 and evidence["per_seed"][2]["oracle_status"] == "fail"
    assert calls[0]["seeds"] == [1001, 1002, 1003]
    assert (calls[0]["view"] / "graphs/expert_t1.yaml").read_text() == "nodes: []\n"
    assert (calls[0]["view"] / "pyproject.toml").is_file()
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert evidence["controller_commit"] == head
    assert "1001" not in json.dumps(evidence)  # CSE-9: seed values stay private
    assert evidence["exposure"] == {
        "commands": 120,
        "safety_events": 0,
        "unit": "command",
        "error": None,
    }
    assert (session / "heldout" / "heldout-evidence.json").is_file()


@pytest.mark.parametrize(
    "defect", ["not_intact", "drifted", "overlap", "commitment", "rule", "threshold"]
)
def test_unbound_inputs_refuse(tmp_path, defect):
    """CSE-2 / CSE-9 / BND-13: a non-intact session, a drifted snapshot, seed overlap, a
    commitment that is not the registered one, or an unresolved rule refuse."""
    session = _session(tmp_path, intact=defect != "not_intact")
    request = _request(
        tmp_path, session, seeds=(0, 1001) if defect == "overlap" else (1001, 1002, 1003)
    )
    if defect == "drifted":
        (session / "final/graphs/expert_t1.yaml").write_text("nodes: [changed]\n")
    elif defect == "commitment":
        (tmp_path / "manifest.json").write_text(
            json.dumps({"seed_commitment": "sha256:" + "0" * 64})
        )
    elif defect == "rule":
        request["rule"] = "majority"
    elif defect == "threshold":
        request["rule"]["threshold"] = 0
    with pytest.raises(he.HeldoutError):
        he.run_heldout(request, launcher=_launcher([]), verdicts=_verdicts([]), exposure=_exposure)
    assert not (session / "heldout" / "heldout-evidence.json").exists()


def test_launch_failure_versus_infrastructure_refusal(tmp_path):
    """CSE-2 / CSE-14: a deliverable that fails to launch is a failed session; a gate
    refusal or infrastructure failure is not an established evaluation (ok false)."""
    session = _session(tmp_path, arm="monolithic")
    failed = he.run_heldout(
        _request(tmp_path, session, threshold=1),
        launcher=_launcher([], {"ok": False, "error": "SyntaxError: bad module"}),
        verdicts=_verdicts([]),
        exposure=_exposure,
    )
    assert failed["ok"] is True and failed["session_success"] is False
    assert failed["launch"] == {
        "ok": False,
        "refused": False,
        "error": "SyntaxError: bad module",
        "refusal": None,
        "infrastructure_invalid": False,
        "rc": None,
    }
    refused = he.run_heldout(
        _request(tmp_path / "b", _session(tmp_path / "b"), threshold=1),
        launcher=_launcher([], {"ok": False, "refused": {"gate": "env_hash"}}),
        verdicts=_verdicts([]),
        exposure=_exposure,
    )
    assert (
        refused["ok"] is False
        and refused["session_success"] is False
        and refused["launch"]["refused"] is True
    )


def test_launch_arguments_run_the_arm_launcher_inside_the_view(tmp_path, monkeypatch):
    """CSE-2 / CSE-9: the launcher is the harness CLI in a separate process rooted at
    the rebuilt view, with the realistic verifier driving the loop."""
    seen = {}

    def fake_run(command, cwd, capture_output, timeout):
        seen.update(command=command, cwd=cwd)

        class Done:
            returncode = 0
            stdout = b'{"ok": true}\n'

        return Done()

    monkeypatch.setattr(he.subprocess, "run", fake_run)
    from aisle.harness.candidates import launch_fields_for

    fields = launch_fields_for(None)
    report = he.launch_arm("monolithic", fields, tmp_path, [7, 9], "heldout-x", 30, {})
    assert report["ok"] is True and seen["cwd"] == tmp_path
    command = seen["command"]
    assert command[:5] == [sys.executable, "-B", "-m", "aisle.harness.cli", "monolith"]
    assert command[command.index("--verifier") + 1] == "realistic"
    assert command[command.index("--root") + 1] == str(tmp_path)
    assert command[command.index("--seeds") + 1] == "7,9"


def test_oracle_verdicts_cover_relaunch_traces_and_refuse_duplicates(tmp_path):
    """BND-3 / ADR-23: verdicts are read from every trace directory of the run, keyed
    by goal id; a duplicate goal id refuses; no oracle rows means no verdicts."""
    import pyarrow as pa

    def write(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table({"sim_time_ns": [1] * len(rows), "text": [json.dumps(r) for r in rows]})
        with pa.ipc.new_stream(path, table.schema) as writer:
            writer.write_table(table)

    run = tmp_path / "runs/heldout-x"
    write(
        run / "traces/verifier-oracle__episode_result.arrow",
        [{"goal_id": "ep-0000", "status": "success"}],
    )
    write(
        run / "traces/relaunch-1/verifier-oracle__episode_result.arrow",
        [{"goal_id": "ep-0001", "status": "fail", "failure": "never_grasped"}],
    )
    verdicts = he.oracle_verdicts(run)
    assert verdicts == {
        "ep-0000": {"status": "success", "failure": None},
        "ep-0001": {"status": "fail", "failure": "never_grasped"},
    }
    assert he.oracle_verdicts(tmp_path / "runs/empty") == {}
    write(
        run / "traces/relaunch-2/verifier-oracle__episode_result.arrow",
        [{"goal_id": "ep-0000", "status": "fail"}],
    )
    with pytest.raises(he.HeldoutError, match="duplicate"):
        he.oracle_verdicts(run)


def test_cli_emits_json_and_exit_status(tmp_path, monkeypatch, capsys):
    """CON-8: the heldout-eval subcommand prints one JSON object; exit 0 iff ok."""
    sys.path.insert(0, str(ROOT / "tools"))
    import matched_campaign

    session = _session(tmp_path)
    request = _request(tmp_path, session, threshold=1)
    (tmp_path / "request.json").write_text(json.dumps(request))
    monkeypatch.setattr(he, "launch_arm", _launcher([]))
    monkeypatch.setattr(he, "oracle_verdicts", _verdicts(["success", "fail", "fail"]))
    rc = matched_campaign.main(["heldout-eval", "--request", str(tmp_path / "request.json")])
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rc == 0, printed
    assert printed["ok"] is True and printed["session_success"] is True
    assert printed["eligible_for_estimate"] is False
    rc = matched_campaign.main(["heldout-eval", "--request", str(tmp_path / "request.json")])
    refused = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rc == 2 and refused["ok"] is False and "resumed" in refused["error"]
