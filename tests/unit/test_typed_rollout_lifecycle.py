"""MON-12/MON-13: rollout retains staged launch identities through its normal lifecycle."""

import hashlib
import json
from pathlib import Path

import pytest
from test_idea_gate import _fake_root

pytestmark = pytest.mark.unit


def _fixture(tmp_path, monkeypatch):
    from aisle.harness import rollout, typed_graph_audit, typed_graph_stage
    from aisle.harness.validate import validate

    root = _fake_root(tmp_path)
    graph = root / "graphs/expert_t1.yaml"
    stages, checked = [], []

    def factory(index):
        stage = root / f"stage-{index}"
        stage.mkdir()
        (stage / "worker.frame").write_bytes(b"retained frame")
        record = {"immutable_id": f"stage-{index}", "snapshot_id": "one-snapshot"}
        stages.append(index)
        return stage, record

    def validation(stage, record, **kwargs):
        checked.append((record["immutable_id"], kwargs["embodiment"]))
        assert kwargs["authored_bytes"] == graph.read_bytes()
        return validate(graph, root, kwargs["embodiment"], False), root

    # Stage construction and OS adapter behavior have separate real-child
    # integration tests. Here the seams isolate the rollout lifecycle itself.
    monkeypatch.setattr(typed_graph_stage, "validation_for_rollout_gates", validation)
    monkeypatch.setattr(
        typed_graph_stage,
        "preflight_graph_stage",
        lambda stage, record: {
            "runtime": {"python_sha256": "fixture", "runtime_id": "fixture"},
        },
    )
    monkeypatch.setattr(
        typed_graph_stage,
        "transport_for_instrumentation",
        lambda stage, record, **kw: (graph.read_text(), root),
    )
    monkeypatch.setattr(
        rollout,
        "resolve_sim_identity",
        lambda extra: {
            "ok": True,
            "sim_extra": extra,
            "sim_backend": "genesis",
            "sim_device": "fixture",
        },
    )
    monkeypatch.setattr(rollout, "_terminate", lambda _: None)
    monkeypatch.setattr(rollout, "reap_orphans", lambda _: None)

    def audit(stage, record):
        from aisle.harness.typed_snapshot import _digest

        report = {
            "schema_version": "aisle.typed-graph-audit.v1",
            "stage_root": str(stage),
            "ok": True,
            "stage_id": record["immutable_id"],
            "errors": [],
            "files": {"worker.frame": hashlib.sha256(b"retained frame").hexdigest()},
            "process_tree_verified": False,
        }
        report["immutable_id"] = _digest(report)
        return report

    monkeypatch.setattr(typed_graph_audit, "audit_graph_stage", audit)

    def spawn(exec_graph, run_dir, env, **kwargs):
        Path(env["AISLE_RESULTS"]).write_text(
            json.dumps({"episode": 0, "seed": 7, "status": "success", "failure": None}) + "\n"
        )

        class Process:
            pid = 0

            def poll(self):
                return 0

        return Process()

    monkeypatch.setattr(rollout, "_spawn_dora", spawn)
    kwargs = dict(
        root=root,
        graph=graph,
        tier="T1",
        episodes=1,
        seeds=[7],
        reset_mode="teleport",
        verifier="oracle",
        run_id="typed-lifecycle",
        branch="fixture",
        no_idea_gate=True,
        env_baseline="local",
        typed_stage_factory=factory,
    )
    return kwargs, stages, checked


def test_rollout_connects_stage_factory_to_gates_instrumentation_and_manifest(
    tmp_path, monkeypatch
):
    """MON-12/HAR-4: authored identity and the selected execution stage remain distinct evidence."""
    from aisle.harness.rollout import rollout

    kwargs, stages, checked = _fixture(tmp_path, monkeypatch)
    authored = kwargs["graph"].read_bytes()
    result = rollout(**kwargs)
    assert result["ok"], result
    assert result["campaign_purpose"] == "expert_parity"
    assert stages == [0]
    assert checked
    manifest = json.loads((tmp_path / "runs/typed-lifecycle/manifest.json").read_text())
    assert manifest["campaign_purpose"] == "expert_parity"
    assert manifest["graph_hash"] == hashlib.sha256(authored).hexdigest()
    assert manifest["typed_stages"][0]["stage_id"] == "stage-0"
    assert manifest["typed_stages"][0]["launch"] == 0
    assert len(manifest["typed_postflight"]) == 1
    audit = manifest["typed_postflight"][0]
    assert audit["stage_id"] == "stage-0"
    assert audit["ok"]
    retained = tmp_path / "runs/typed-lifecycle/typed-postflight-0.json"
    assert json.loads(retained.read_text()) == audit
    collection = manifest["typed_artifacts"][0]
    assert collection["report"]["ok"]
    artifact = tmp_path / "runs/typed-lifecycle" / collection["path"] / "raw/worker.frame"
    assert artifact.read_bytes() == b"retained frame"
    from aisle.harness.matched_evidence import retain_run

    evidence = retain_run(
        tmp_path / "runs/typed-lifecycle", tmp_path / "session-evidence", run_id="typed-lifecycle"
    )
    assert evidence["ok"], evidence
    assert "typed-artifacts-0/raw/worker.frame" in evidence["files"]
    assert (
        tmp_path / "session-evidence/raw/typed-artifacts-0/raw/worker.frame"
    ).read_bytes() == b"retained frame"


def test_rollout_refuses_failed_stage_before_dora_spawn(tmp_path, monkeypatch):
    """MON-6/MON-13: stage construction failure is infrastructure-invalid before execution."""
    from aisle.harness import rollout

    kwargs, _, _ = _fixture(tmp_path, monkeypatch)

    def refused(index):
        raise ValueError("stage fixture refusal")

    kwargs["typed_stage_factory"] = refused
    monkeypatch.setattr(rollout, "_spawn_dora", lambda *a, **kw: pytest.fail("Dora started"))
    result = rollout.rollout(**kwargs)
    assert not result["ok"]
    assert result["infrastructure_invalid"]
    assert result["refused"]["gate"] == "typed_stage"


@pytest.mark.parametrize(
    "reuse,audit_failure,spawn_failure",
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
def test_timeout_relaunch_selects_fresh_stage_or_records_infrastructure_failure(
    tmp_path, monkeypatch, reuse, audit_failure, spawn_failure
):
    """MON-12/MON-13: a timed-out incarnation cannot reuse its host stage or evidence paths."""
    from aisle.harness import rollout

    kwargs, stages, _ = _fixture(tmp_path, monkeypatch)
    original_factory = kwargs["typed_stage_factory"]
    if audit_failure:
        from aisle.harness import typed_graph_audit

        monkeypatch.setattr(
            typed_graph_audit,
            "audit_graph_stage",
            lambda stage, record: {
                "ok": False,
                "stage_id": record["immutable_id"],
                "errors": ["missing host"],
            },
        )

    def factory(index):
        stage, record = original_factory(index)
        if reuse and index:
            stage = tmp_path / "stage-0"
            record["immutable_id"] = "stage-0"
        return stage, record

    class Clock:
        t = 0.0

        def monotonic(self):
            return self.t

        def time(self):
            return self.t

        def sleep(self, seconds):
            self.t += seconds

    class Process:
        pid = 0

        def poll(self):
            return None

    spawns = []

    def spawn(exec_graph, run_dir, env, **unused):
        spawns.append(env["AISLE_SEEDS"])
        if spawn_failure and len(spawns) == 2:
            raise OSError("relaunch spawn fixture failure")
        with Path(env["AISLE_RESULTS"]).open("a") as stream:
            if len(spawns) == 1:
                stream.write(json.dumps({"episode": 0, "seed": 7, "status": "success"}) + "\n")
            else:
                stream.write(json.dumps({"episode": 2, "seed": 9, "status": "success"}) + "\n")
        return Process()

    monkeypatch.setattr(rollout, "time", Clock())
    monkeypatch.setattr(rollout, "GENESIS_BUILD_BUDGET_S", 0)
    monkeypatch.setattr(rollout, "resolve_budgets", lambda *a, **kw: (60.0, 1.0))
    monkeypatch.setattr(rollout, "_spawn_dora", spawn)
    kwargs.update(
        typed_stage_factory=factory,
        episodes=3,
        seeds=[7, 8, 9],
        timeout_s=100,
        record_simulator_work=True,
    )
    if spawn_failure:
        with pytest.raises(OSError, match="relaunch spawn fixture failure"):
            rollout.rollout(**kwargs)
        assert stages == [0, 1]
        audit = json.loads((tmp_path / "runs/typed-lifecycle/typed-postflight-1.json").read_text())
        assert audit["stage_id"] == "stage-1"
        return
    result = rollout.rollout(**kwargs)
    assert stages == ([0] if audit_failure else [0, 1])
    summary = json.loads(
        (tmp_path / "runs/typed-lifecycle/simulator-work-summary.json").read_text()
    )
    assert len(summary["launches"]) == len(spawns)
    assert not summary["journals_complete"]
    for index in range(len(spawns)):
        receipt = tmp_path / f"runs/typed-lifecycle/simulator-work/launch-{index}.expected.json"
        assert json.loads(receipt.read_text())["launch"] == index
    manifest = json.loads((tmp_path / "runs/typed-lifecycle/manifest.json").read_text())
    assert [audit["stage_id"] for audit in manifest["typed_postflight"]] == (
        ["stage-0"] if reuse or audit_failure else ["stage-0", "stage-1"]
    )
    if audit_failure:
        assert not result["ok"]
        assert result["infrastructure_invalid"]
        assert len(spawns) == 1
        assert manifest["typed_stage_error"]["launch"] == 0
        assert manifest["typed_postflight"][0]["ok"] is False
    elif reuse:
        assert not result["ok"]
        assert result["infrastructure_invalid"]
        assert len(spawns) == 1
        assert manifest["typed_stage_error"]["launch"] == 1
        assert "reused" in manifest["typed_stage_error"]["error"]
    else:
        assert result["ok"], result
        assert len(spawns) == 2
        assert [row["stage_id"] for row in manifest["typed_stages"]] == ["stage-0", "stage-1"]
        assert manifest["typed_stage_error"] is None


@pytest.mark.parametrize("mutation", ["snapshot", "runtime"])
def test_stage_sequence_refuses_changed_source_or_runtime(tmp_path, monkeypatch, mutation):
    """MON-13: a fresh stage path cannot conceal a different source or runtime on relaunch."""
    from aisle.harness import typed_graph_stage

    kwargs, _, _ = _fixture(tmp_path, monkeypatch)
    original = kwargs["typed_stage_factory"]

    def factory(index):
        stage, record = original(index)
        if index and mutation == "snapshot":
            record["snapshot_id"] = "different-snapshot"
        return stage, record

    monkeypatch.setattr(
        typed_graph_stage,
        "preflight_graph_stage",
        lambda stage, record: {
            "runtime": "different"
            if mutation == "runtime" and record["immutable_id"] == "stage-1"
            else "original",
        },
    )
    history = []
    options = dict(
        authored_bytes=kwargs["graph"].read_bytes(),
        graph=kwargs["graph"],
        controller_root=kwargs["root"],
        embodiment="franka",
    )
    typed_graph_stage.select_rollout_stage(factory, 0, history, **options)
    with pytest.raises(RuntimeError, match="snapshot or runtime"):
        typed_graph_stage.select_rollout_stage(factory, 1, history, **options)
    assert len(history) == 1


@pytest.mark.parametrize("raises", [False, True])
def test_postflight_failure_invalidates_completed_rollout_after_teardown(
    tmp_path, monkeypatch, raises
):
    """MON-12/MON-13: episode completion cannot conceal a missing or failed postflight audit."""
    from aisle.harness import rollout, typed_graph_audit

    kwargs, _, _ = _fixture(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(rollout, "_terminate", lambda _: events.append("terminate"))
    monkeypatch.setattr(rollout, "reap_orphans", lambda _: events.append("reap"))

    def audit(stage, record):
        assert events == ["terminate", "reap"]
        events.append("audit")
        if raises:
            raise OSError("audit fixture unavailable")
        return {"ok": False, "stage_id": record["immutable_id"], "errors": ["missing host"]}

    monkeypatch.setattr(typed_graph_audit, "audit_graph_stage", audit)
    result = rollout.rollout(**kwargs)
    assert not result["ok"]
    assert result["infrastructure_invalid"]
    assert events == ["terminate", "reap", "audit"]
    manifest = json.loads((tmp_path / "runs/typed-lifecycle/manifest.json").read_text())
    assert manifest["typed_postflight"][0]["ok"] is False
    assert manifest["typed_stage_error"]["launch"] == 0


def test_collection_drift_invalidates_otherwise_successful_run(tmp_path, monkeypatch):
    """MON-12/MON-13: audit-to-copy drift remains retained and invalidates the run."""
    from aisle.harness import rollout, typed_graph_audit

    kwargs, _, _ = _fixture(tmp_path, monkeypatch)
    original = typed_graph_audit.audit_graph_stage

    def changed(stage, record):
        audit = original(stage, record)
        (stage / "worker.frame").write_bytes(b"changed after audit")
        return audit

    monkeypatch.setattr(typed_graph_audit, "audit_graph_stage", changed)
    result = rollout.rollout(**kwargs)
    assert not result["ok"]
    assert result["infrastructure_invalid"]
    manifest = json.loads((tmp_path / "runs/typed-lifecycle/manifest.json").read_text())
    assert manifest["typed_postflight"][0]["ok"]
    assert not manifest["typed_artifacts"][0]["report"]["ok"]
    raw = tmp_path / "runs/typed-lifecycle/typed-artifacts-0/raw/worker.frame"
    assert raw.read_bytes() == b"changed after audit"


@pytest.mark.parametrize("spawn_failure", [False, True])
def test_rollout_reserves_work_before_spawn_and_retains_missing_producer(
    tmp_path, monkeypatch, spawn_failure
):
    """MON-12/CSE-4: even failure before bridge startup retains expected launch accounting."""
    from aisle.harness import rollout

    kwargs, _, _ = _fixture(tmp_path, monkeypatch)
    original_spawn = rollout._spawn_dora

    def spawn(exec_graph, run_dir, env, **options):
        receipt = json.loads((run_dir / "simulator-work/launch-0.expected.json").read_text())
        assert receipt["graph_sha256"] == hashlib.sha256(exec_graph.read_bytes()).hexdigest()
        assert receipt["run_id"] == kwargs["run_id"]
        if spawn_failure:
            raise OSError("spawn fixture failure")
        return original_spawn(exec_graph, run_dir, env, **options)

    monkeypatch.setattr(rollout, "_spawn_dora", spawn)
    if spawn_failure:
        with pytest.raises(OSError, match="spawn fixture failure"):
            rollout.rollout(**kwargs, record_simulator_work=True)
    else:
        rollout.rollout(**kwargs, record_simulator_work=True)
    result = json.loads((tmp_path / "runs/typed-lifecycle/simulator-work-summary.json").read_text())
    assert not result["journals_complete"]
    assert len(result["launches"]) == 1
    assert result["launches"][0]["error"]


def test_teardown_failure_still_reaps_audits_and_retains_work(tmp_path, monkeypatch):
    """MON-12/MON-13: one cleanup failure cannot skip later cleanup or launch evidence."""
    from aisle.harness import rollout

    kwargs, _, _ = _fixture(tmp_path, monkeypatch)
    reaped = []

    def terminate(process):
        raise OSError("termination fixture failure")

    monkeypatch.setattr(rollout, "_terminate", terminate)
    monkeypatch.setattr(rollout, "reap_orphans", lambda directory: reaped.append(directory))
    with pytest.raises(OSError, match="termination fixture failure"):
        rollout.rollout(**kwargs, record_simulator_work=True)
    run = tmp_path / "runs/typed-lifecycle"
    assert reaped == [run]
    assert (run / "typed-postflight-0.json").is_file()
    summary = json.loads((run / "simulator-work-summary.json").read_text())
    assert len(summary["launches"]) == 1
    assert not summary["journals_complete"]
