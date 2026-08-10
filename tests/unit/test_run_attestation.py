"""Attestation of the EXECUTED dataflow (issue #128) and the rung-filtered
recorder subscription (TC-9): an L1 run's trace cannot carry the
NON-privileged ground-truth pose endpoint (`poses`), rather than relying on
the bridge's runtime restraint alone. `oracle_state` is deliberately still
recorded at every rung — it is the verifier's privileged input, governed by
VAL-6/ADR-27, not by the rung (round-2 review: the earlier docstring's
"physically unable to contain ground-truth pose" overclaimed)."""

from pathlib import Path

import pytest
import yaml

from aisle.harness.rollout import instrumented_graph

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _recorder_inputs(graph_path, tmp_path):
    (tmp_path / "run").mkdir(exist_ok=True)
    out = instrumented_graph(graph_path, REPO_ROOT, tmp_path / "run")
    doc = yaml.safe_load(out.read_text())
    recorder = next(n for n in doc["nodes"] if n["id"] == "trace-recorder")
    return recorder["inputs"]


def test_recorder_never_subscribes_to_rung_forbidden_bridge_topics(tmp_path):
    """Issue #128 question 2: the recorder auto-subscribes to every DECLARED
    output — including one the graph declares but the rung forbids the
    bridge from ever publishing (the #128 repro shape). Filtering by rung
    makes an L1 trace provable on its own terms: no poses endpoint exists
    in the artifact at all."""
    doc = yaml.safe_load((REPO_ROOT / "graphs" / "expert_t1.yaml").read_text())
    bridge = next(n for n in doc["nodes"] if n["id"] == "dora-genesis")
    bridge["outputs"].append("poses")  # declared, unrouted, never published at L1
    graph = tmp_path / "expert_t1_declared_poses.yaml"
    # keep node paths resolvable exactly as graphs/ layout expects
    for node in doc["nodes"]:
        node["path"] = str((REPO_ROOT / "graphs" / node["path"]).resolve())
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))

    inputs = _recorder_inputs(graph, tmp_path)
    assert "dora-genesis__poses" not in inputs
    assert "dora-genesis__seg_overhead" in inputs  # the rung's own topic stays


def test_recorder_still_subscribes_to_poses_at_l0(tmp_path):
    """The filter is the RUNG's, not a blanket ban: at L0 `poses` is the
    sanctioned pose source and its trace endpoint is evidence, not leakage."""
    inputs = _recorder_inputs(REPO_ROOT / "graphs" / "expert_t0.yaml", tmp_path)
    assert "dora-genesis__poses" in inputs


def test_manifest_attests_authored_and_executed_hashes_end_to_end(tmp_path, monkeypatch):
    """Round-2 review P1: the attestation FIELDS this concern exists for
    (#128) were asserted nowhere — deleting the exec hash append or
    recording the authored hash twice passed the whole suite. This drives
    the REAL rollout() end to end (gates, instrumentation, manifest) with
    only the dora spawn stubbed, and pins each hash to independently
    computed bytes: graph_hash == sha256(authored file), exec_graph_hashes
    == [sha256(the instrumented copy on disk)], and the two differ."""
    import hashlib
    import json

    from test_idea_gate import _fake_root

    from aisle.harness import rollout as rollout_module

    root = _fake_root(tmp_path)
    graph = root / "graphs" / "expert_t1.yaml"

    def fake_spawn(exec_graph, run_dir, env):
        results = Path(env["AISLE_RESULTS"])
        seeds = [int(s) for s in env["AISLE_SEEDS"].split(",")]
        with open(results, "w") as f:
            for i, seed in enumerate(seeds):
                f.write(
                    json.dumps({"episode": i, "seed": seed, "status": "success", "failure": None})
                    + "\n"
                )

        class FakeProc:
            pid = 0

            def poll(self):
                return None

        return FakeProc()

    monkeypatch.setattr(rollout_module, "_spawn_dora", fake_spawn)
    monkeypatch.setattr(rollout_module, "_terminate", lambda proc: None)

    report = rollout_module.rollout(
        root=root,
        graph=graph,
        tier="T1",
        episodes=2,
        seeds=[0, 1],
        reset_mode="teleport",
        verifier="oracle",
        run_id="attest-unit",
        branch="b",
        no_idea_gate=True,
        env_baseline="local",
        perception="L1",
    )
    assert report["ok"] is True, report
    manifest = json.loads((root / "runs" / "attest-unit" / "manifest.json").read_text())
    assert manifest["graph_hash"] == hashlib.sha256(graph.read_bytes()).hexdigest()
    exec_copy = root / "runs" / "attest-unit" / "graph.yaml"
    assert manifest["exec_graph_hashes"] == [hashlib.sha256(exec_copy.read_bytes()).hexdigest()]
    assert manifest["exec_graph_hashes"][0] != manifest["graph_hash"]
    assert manifest["perception"] == "L1"


def test_instrumentation_fails_closed_when_the_rung_is_unresolvable(tmp_path):
    """Round-2 review: instrumented_graph re-reads graph and registry at
    launch and at every wall-clamp relaunch, hours after the HAR-2 gate —
    a registry broken in between must REFUSE loudly, never silently record
    an unfiltered trace (the fail-open turned the L1 declaration into a
    rung error and disabled the filter)."""
    import shutil

    from test_idea_gate import _fake_root

    root = _fake_root(tmp_path)
    # replace the symlinked registry with a writable copy, then break the
    # bridge manifest AFTER "gate time"
    real_registry = (root / "registry").resolve()
    (root / "registry").unlink()
    shutil.copytree(real_registry, root / "registry")
    (root / "registry" / "manifests" / "dora-genesis.yaml").write_text("id: [broken\n")
    (tmp_path / "run").mkdir()
    with pytest.raises(RuntimeError, match="rung unresolvable|perception rung"):
        instrumented_graph(root / "graphs" / "expert_t1.yaml", root, tmp_path / "run")


def test_settle_records_actual_episode_count(tmp_path, monkeypatch):
    """ADR-21: the settle entry must carry the ACTUAL episode count. The
    wiring bug this pins: settle ran inside the finally while
    episode_records was parsed after it, so every trusted run settled at 0
    and the campaign episode ceiling never decremented — found by the first
    real trusted run (p2-a1-t1-l0-e50 reserved 50, settled 0). The fake
    root becomes its own git origin so the REAL trusted reserve/settle path
    runs; a draft of this test used env_baseline=local and passed vacuously
    because local never reserves — the defect class, in a test draft."""
    import json
    import subprocess

    from test_idea_gate import _fake_root

    from aisle.harness import rollout as rollout_module

    root = _fake_root(tmp_path)
    # the trusted gate demands dist attestation evidence (ADR-24 D2/D3);
    # extend the fixture's stub checker to supply it
    (root / "tools" / "env_hash.py").write_text(
        "import json\n"
        'print(json.dumps({"ok": True, "env_hash": "h", '
        '"dist": {"attested": True, "env_fingerprint": "f"}}))\n'
    )
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "root"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(root)], cwd=root, check=True, capture_output=True
    )

    def fake_spawn(exec_graph, run_dir, env):
        results = Path(env["AISLE_RESULTS"])
        with open(results, "w") as f:
            for i, seed in enumerate(int(s) for s in env["AISLE_SEEDS"].split(",")):
                f.write(
                    json.dumps({"episode": i, "seed": seed, "status": "success", "failure": None})
                    + "\n"
                )

        class FakeProc:
            pid = 0

            def poll(self):
                return None

        return FakeProc()

    monkeypatch.setattr(rollout_module, "_spawn_dora", fake_spawn)
    monkeypatch.setattr(rollout_module, "_terminate", lambda proc: None)
    report = rollout_module.rollout(
        root=root,
        graph=root / "graphs" / "expert_t1.yaml",
        tier="T1",
        episodes=3,
        seeds=[0, 1, 2],
        reset_mode="teleport",
        verifier="oracle",
        run_id="settle-unit",
        branch="b",
        no_idea_gate=True,
        env_baseline="origin/main",
        perception="L1",
    )
    assert report["ok"] is True, report
    entries = [
        json.loads(line)
        for line in (root / "runs" / "campaign_ledger.jsonl").read_text().splitlines()
        if line.strip()
    ]
    kinds = {e["kind"]: e for e in entries}
    assert kinds["reserve"]["episodes"] == 3
    assert kinds["settle"]["episodes"] == 3, entries
    assert rollout_module.budget_remaining(root)["episodes_left"] == 500 - 3
