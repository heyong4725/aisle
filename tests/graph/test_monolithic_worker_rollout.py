"""MON-4/MON-12: real monolithic worker, primitive broker and ordinary simulation.

Engineering integration only: no participant frontend, parity gate, or estimate.
Each run retains its own outcome; no per-seed physics success is required.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.sim,
    pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS worker confinement"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_monolithic_worker_rollout_retains_real_episode_and_worker_exit(tmp_path, monkeypatch):
    """MON-4/MON-12: actual source-bound worker controls the ordinary engineering rollout."""
    pytest.importorskip("genesis")
    cli = shutil.which("dora")
    if cli is None:
        pytest.skip("source-pinned Dora CLI is unavailable")
    prefix = Path(cli).resolve().parents[1]
    if not (prefix / "aisle-dora-receipt.json").is_file():
        pytest.skip("Dora installation has no source-pin receipt")
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    import numpy
    import pyarrow
    from dora_runtime import verify
    from test_monolith_worker_launch import _worker_interpreter

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.monolith import check_module, stamp_graph
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
    from aisle.harness.rollout import rollout
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.worker_declaration import provision_worker_declaration
    from aisle.monolith.worker_config import configured_worker_factory

    base = tmp_path.resolve()
    run_id = "monolithic-worker-integration-" + uuid4().hex
    identity = verify(ROOT / "dora-runtime.json", prefix)
    assert identity["acceptance_ready"], identity
    (base / "dora-runtime.json").write_text(json.dumps(identity, indent=2))
    private = base / "private"
    private.mkdir()
    views = {arm: base / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "experts/monolithic/expert_t1.py", module)
    packages = base / "codecs"
    packages.mkdir()
    for package in (numpy, pyarrow):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    python, runtime_root = _worker_interpreter()
    runtime = capture_runtime((runtime_root, packages))
    adapter = hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest()
    declaration = provision_worker_declaration(
        arm="monolithic",
        bundle=base / "bundle",
        home=base / "home",
        evidence=private / "declaration",
        hidden_roots=(ROOT, *views.values(), private),
        runtime_record=runtime,
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=adapter,
        timeout_s=360,
    )
    preparation = prepare_monolithic_run(
        controller_root=ROOT,
        views=views,
        output=private / "preparation",
        declaration=declaration,
        runtime=runtime,
        adapter=adapter,
        embodiment="franka",
    )
    (base / "preparation.json").write_text(json.dumps(preparation, indent=2))
    factory = configured_worker_factory(
        preparation["worker_config"], preparation["worker_config_sha256"], phase="check"
    )
    pre = check_module(module, "franka", worker_factory=factory)
    (base / "check.json").write_text(json.dumps(pre, indent=2, default=str))
    assert pre["ok"], pre
    # Use the ordinary graph stamper and rollout's logged engineering override.
    # monolith.run does not expose env_baseline; do not charge a campaign ledger.
    graph = stamp_graph(ROOT, module, ROOT / "graphs/out", **preparation)
    monkeypatch.setenv("UV_NO_SYNC", "1")
    result = rollout(
        root=ROOT,
        graph=graph,
        tier="T1",
        episodes=1,
        seeds=[7],
        reset_mode="teleport",
        verifier="oracle",
        run_id=run_id,
        branch="feat/519-matched-session",
        no_idea_gate=True,
        timeout_s=300,
        embodiment="franka",
        env_baseline="local",
        perception="L1",
        sim_extra="sim",
        per_episode_wall_s=120,
        record_simulator_work=True,
    )
    (base / "result.json").write_text(json.dumps(result, indent=2, default=str))
    assert result["ok"], result
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["seed"] == 7
    assert result["episodes"][0]["verifier"] == "oracle"
    assert not result["episodes"][0].get("synthetic", False)
    retained = ROOT / "runs" / run_id
    assert (retained / "manifest.json").is_file()
    assert [
        json.loads(line) for line in (retained / "episodes.jsonl").read_text().splitlines()
    ] == result["episodes"]
    for phase in ("check", "run"):
        record = json.loads(
            (private / "preparation/monolithic-execution" / phase / "rpc/worker.json").read_text()
        )
        assert record["state"] == "closed" and record["rc"] == 0, record
        assert record["error"] is None, record

    from aisle.harness.matched_evidence import retain_run

    collection = retain_run(ROOT / "runs" / run_id, base / "retained-run", run_id=run_id)
    assert collection["ok"], collection["error"]
    accounting = collection["simulator_work"]
    assert accounting["status"] == "recomputed", accounting
    summary = accounting["summary"]
    assert len(summary["launches"]) == 1
    launch = summary["launches"][0]
    assert launch["attempted"]["build"] == 1
    assert launch["completed"]["reset"] >= 1
    assert launch["completed"]["step"] > 0
    assert launch["schema_version"] == "aisle.simulator-work-journal.v3"
    assert launch["completed"]["physics_step"] >= launch["completed"]["step"] + 1
    assert launch["completed_env_steps"] == launch["completed"]["physics_step"]
    assert summary["completed_env_sim_ns"] > 0
    assert summary["producer_coverage_complete"] is False
