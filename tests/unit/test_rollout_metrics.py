"""SPEC 070 rollout metrics and instrumentation (HAR-1, HAR-3, HAR-4) —
pure pieces, no dora, no sim (CON-12)."""

import json
from pathlib import Path

import pytest
import yaml

from aisle.harness.rollout import compute_metrics, instrumented_graph, parse_seed_range

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def episode(status, failure=None, retries=0):
    return {"status": status, "failure": failure, "retries": retries, "t_end": 10.0}


def test_pass8_is_in_context_retries_never_best_of_8():
    """HAR-3: pass@8 counts an episode that succeeded within <=8 IN-CONTEXT
    retries; a first-attempt success counts toward both; a success after
    retries counts toward pass8 only. It is NEVER best-of-8 independent
    episodes: the metric is per-episode, so N episodes contribute exactly
    N samples to both denominators."""
    episodes = [
        episode("success"),  # pass1 and pass8
        episode("success", retries=3),  # pass8 only
        episode("fail", "timeout"),
        episode("fail", "dropped", retries=8),
    ]
    metrics = compute_metrics(episodes)
    assert metrics["pass1"] == pytest.approx(1 / 4)
    assert metrics["pass8"] == pytest.approx(2 / 4)
    assert metrics["failures"] == {"timeout": 1, "dropped": 1}


def test_failure_histogram_covers_ver3_classes():
    episodes = [episode("fail", c) for c in ("wrong_object", "dropped", "timeout")]
    assert compute_metrics(episodes)["failures"] == {
        "wrong_object": 1,
        "dropped": 1,
        "timeout": 1,
    }


def test_seed_range_forms():
    assert parse_seed_range("0..3") == [0, 1, 2, 3]
    assert parse_seed_range("7") == [7]
    assert parse_seed_range("1,4,9") == [1, 4, 9]


def test_instrumented_graph_adds_recorder_and_absolutizes(tmp_path):
    """HAR-4: the executable copy gains a trace-recorder wired to every
    traceable topic that exists in the graph, node paths are absolute
    (dora cwd = the run dir), and the ORIGINAL graph file is untouched."""
    original = (REPO_ROOT / "graphs" / "expert_t0.yaml").read_text()
    out = instrumented_graph(REPO_ROOT / "graphs" / "expert_t0.yaml", REPO_ROOT, tmp_path)
    doc = yaml.safe_load(out.read_text())
    recorder = next(n for n in doc["nodes"] if n["id"] == "trace-recorder")
    sources = {port: spec["source"] for port, spec in recorder["inputs"].items()}
    assert sources["dora-genesis__joint_state"] == "dora-genesis/joint_state"
    assert sources["dora-genesis__oracle_state"] == "dora-genesis/oracle_state"
    # EVERY declared node/output endpoint is wired (HAR-4), including both
    # reset_done producers and the image topics (PR #11 review)
    declared = {
        f"{n['id']}__{topic}" for n in doc["nodes"][:-1] for topic in (n.get("outputs") or [])
    }
    assert set(sources) == declared
    assert "dora-genesis__reset_done" in sources and "reset__reset_done" in sources
    assert "dora-genesis__rgb_wrist" in sources
    for node in doc["nodes"]:
        assert Path(node["path"]).is_absolute()
    assert (REPO_ROOT / "graphs" / "expert_t0.yaml").read_text() == original


def test_rollout_relative_root_pins_absolute_paths_for_dora(tmp_path, monkeypatch):
    """HAR-1: rollout() itself must resolve a relative root BEFORE spawning
    dora (whose cwd is the run dir): otherwise AISLE_RESULTS and
    AISLE_TRACE_DIR are relative strings the nodes resolve against dora's
    cwd, sending passing episodes into a nested runs/<id>/runs/<id>/ tree
    the stall watcher never sees (T18 live shakeout, 600 s stall kill).
    Removing root.resolve() in rollout() must fail THIS test, not only the
    instrumented_graph one."""
    from aisle.harness import rollout as ro

    root = tmp_path / "proj"
    (root / "graphs").mkdir(parents=True)
    (root / "graphs" / "g.yaml").write_text(
        "nodes:\n- id: n\n  path: ../src/n.py\n  outputs: [t]\n"
    )
    (root / "harness").mkdir()
    (root / "harness" / "budget.toml").write_text(
        "[campaign]\ntokens = 1\nepisodes = 1\nwall_h = 1\n"
    )
    captured = {}

    class FakeProc:
        pid = 2**22  # nonexistent pgid: the kill path raises ProcessLookupError
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def communicate(self, input=None, timeout=None):
            return ("", "")

    def fake_popen(cmd, cwd=None, env=None, **kwargs):
        # subprocess.run (the git calls) rides the same Popen; only the
        # dora spawn is under test
        if cmd[0] == "dora":
            captured["cwd"] = Path(cwd)
            captured["env"] = env
        proc = FakeProc()
        proc.args = cmd
        return proc

    monkeypatch.setattr(ro.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ro, "run_gates", lambda *a, **k: {"ok": True, "env_hash": "x"})
    monkeypatch.setattr(ro, "reap_orphans", lambda *a, **k: None)
    monkeypatch.chdir(root)
    report = ro.rollout(
        root=Path("."),
        graph=Path("graphs/g.yaml"),
        tier="T0",
        episodes=1,
        seeds=[0],
        reset_mode="teleport",
        verifier="oracle",
        run_id="rootnorm",
        branch="test",
        no_idea_gate=True,
        env_baseline="local",
    )
    assert report["ok"] is False  # the fake dora ran zero episodes
    assert captured["cwd"].is_absolute()
    assert Path(captured["env"]["AISLE_RESULTS"]).is_absolute()
    doc = yaml.safe_load((root / "runs" / "rootnorm" / "graph.yaml").read_text())
    recorder = next(n for n in doc["nodes"] if n["id"] == "trace-recorder")
    assert Path(recorder["env"]["AISLE_TRACE_DIR"]).is_absolute()


def test_instrumented_graph_absolutizes_with_relative_root(tmp_path, monkeypatch):
    """HAR-4: a RELATIVE root (e.g. `--root .`) must still yield absolute
    node paths — dora's cwd is the run dir, so a relative trace-recorder
    path kills the dataflow at startup with zero episodes and no
    diagnostics (live T18 registration shakeout)."""
    monkeypatch.chdir(REPO_ROOT)
    out = instrumented_graph(Path("graphs/expert_t0.yaml"), Path("."), tmp_path)
    doc = yaml.safe_load(out.read_text())
    for node in doc["nodes"]:
        assert Path(node["path"]).is_absolute(), node["id"]


def test_rollout_refuses_unsafe_or_reused_run_ids(tmp_path):
    """PR #11 review: a traversal-shaped run_id must never touch paths
    outside runs/, and an existing run must never be overwritten. Also:
    non-T0 tiers refuse rather than run mislabeled."""
    from aisle.harness.rollout import rollout

    common = dict(
        root=tmp_path,
        graph=REPO_ROOT / "graphs" / "expert_t0.yaml",
        episodes=1,
        seeds=[0],
        reset_mode="teleport",
        verifier="oracle",
        branch="b",
        no_idea_gate=True,
    )
    bad = rollout(tier="T0", run_id="../escape", **common)
    assert bad["ok"] is False and "unsafe run_id" in bad["error"]
    (tmp_path / "runs" / "taken").mkdir(parents=True)
    reused = rollout(tier="T0", run_id="taken", **common)
    assert reused["ok"] is False and "already exists" in reused["error"]
    # tiers propagate to the graph env rather than refusing (HAR-1): the
    # gate stack still refuses this call earlier (no committed env hash in
    # the fake root), proving tier is no longer a refusal cause
    tiered = rollout(tier="T1", run_id="fresh", **common)
    assert "Phase 2" not in str(tiered.get("error", ""))


def test_tier_budgets_scale_for_retail_tiers():
    """RS-6/HAR-1 (PR #21): `harness rollout` is the public path for EVERY
    tier, so retail tiers (S1..S3, store-sim rtf ~0.1) get episode/wall
    budgets a healthy ~25-wall-minute episode fits inside, while desk tiers
    keep the tight ADR-11 budgets."""
    from aisle.harness.rollout import (
        EPISODE_TIMEOUT_S,
        PER_EPISODE_BUDGET_S,
        RETAIL_EPISODE_TIMEOUT_S,
        RETAIL_PER_EPISODE_BUDGET_S,
        tier_budgets,
    )

    for tier in ("S1", "S2", "S3"):
        assert tier_budgets(tier) == (RETAIL_EPISODE_TIMEOUT_S, RETAIL_PER_EPISODE_BUDGET_S)
    for tier in ("T0", "T1", "T2"):
        assert tier_budgets(tier) == (EPISODE_TIMEOUT_S, PER_EPISODE_BUDGET_S)
    # the S1 gate's observed shape: ~101.5 sim s episode, ~28:39 wall total
    # (ADR-18) — the retail budgets must clear both with headroom
    assert RETAIL_EPISODE_TIMEOUT_S > 102
    assert RETAIL_PER_EPISODE_BUDGET_S > 25 * 60


def test_per_episode_wall_clamp_records_and_relaunches(tmp_path, monkeypatch):
    """W/S2 holdout wedge (H3 campaign 2): one episode ran 4 h with
    traces still GROWING (ffmpeg), so the stall detector never fired and
    the wedged episode ate the whole scoring window, masking the
    remaining seeds. rollout() must clamp an episode whose WALL time
    exceeds the tier's per-episode budget: kill the graph, record a
    synthetic wall_clamp failure for that seed, and relaunch for the
    remaining seeds so they still get scored (HAR-1; ADR-23)."""
    from aisle.harness import rollout as ro

    root = tmp_path / "proj"
    (root / "graphs").mkdir(parents=True)
    (root / "graphs" / "g.yaml").write_text("nodes:\n- id: n\n  path: n.py\n  outputs: [t]\n")
    (root / "harness").mkdir()
    (root / "harness" / "budget.toml").write_text(
        "[campaign]\ntokens = 1\nepisodes = 100\nwall_h = 100\n"
    )

    class FakeClock:
        t = 0.0

        def monotonic(self):
            return self.t

        def time(self):
            return self.t

        def sleep(self, s):
            self.t += s

    class FakeProc:
        pid = 2**22  # nonexistent pgid: kill paths raise ProcessLookupError
        returncode = 0

        def __init__(self, alive=False):
            self._alive = alive

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def poll(self):
            return None if self._alive else 0

        def wait(self, timeout=None):
            return 0

        def communicate(self, input=None, timeout=None):
            return ("", "")

    spawns = []

    def fake_popen(cmd, cwd=None, env=None, **kwargs):
        # subprocess.run (the git calls) rides the same Popen; only the
        # dora spawns are under test
        if cmd[0] != "dora":
            proc = FakeProc()
            proc.args = cmd
            return proc
        spawns.append(env["AISLE_SEEDS"])
        results = Path(env["AISLE_RESULTS"])
        with open(results, "a") as f:
            if len(spawns) == 1:  # first launch: seed 0 lands, then wedges
                f.write('{"episode": 0, "seed": 0, "status": "success", "success": true}\n')
            else:  # relaunch: every remaining seed lands
                for i, s in enumerate(env["AISLE_SEEDS"].split(",")):
                    f.write(
                        f'{{"episode": {i}, "seed": {s}, "status": "success", "success": true}}\n'
                    )
        proc = FakeProc(alive=len(spawns) == 1)
        proc.args = cmd
        return proc

    monkeypatch.setattr(ro, "time", FakeClock())
    monkeypatch.setattr(ro.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ro, "run_gates", lambda *a, **k: {"ok": True, "env_hash": "x"})
    monkeypatch.setattr(ro, "reap_orphans", lambda *a, **k: None)
    report = ro.rollout(
        root=root,
        graph=root / "graphs" / "g.yaml",
        tier="T0",
        episodes=3,
        seeds=[0, 1, 2],
        reset_mode="teleport",
        verifier="oracle",
        run_id="clamp",
        branch="test",
        no_idea_gate=True,
        env_baseline="local",
    )
    assert spawns == ["0,1,2", "2"]  # relaunched with only the remaining seed
    assert report["failures"] == {"wall_clamp": 1}
    clamped = [e for e in report["episodes"] if e.get("failure") == "wall_clamp"]
    assert [e["seed"] for e in clamped] == [1]  # the wedged seed, recorded
    assert report["pass1"] == pytest.approx(2 / 3)  # remaining seeds still scored
    manifest = json.loads((root / "runs" / "clamp" / "manifest.json").read_text())
    assert manifest["wall_clamped"] == [1] and manifest["relaunches"] == 1
