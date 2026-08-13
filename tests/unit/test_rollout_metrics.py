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
    monkeypatch.setattr(
        ro,
        "run_gates",
        lambda *a, **k: {
            "ok": True,
            "env_hash": "x",
            "sim_extra": "sim",
            "sim_backend": "metal",
            "sim_device": "mps",
        },
    )
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
        T2_EPISODE_TIMEOUT_S,
        T2_PER_EPISODE_BUDGET_S,
        tier_budgets,
    )

    for tier in ("S1", "S2", "S3"):
        assert tier_budgets(tier) == (RETAIL_EPISODE_TIMEOUT_S, RETAIL_PER_EPISODE_BUDGET_S)
    for tier in ("T0", "T1"):
        assert tier_budgets(tier) == (EPISODE_TIMEOUT_S, PER_EPISODE_BUDGET_S)
    # T2's scan tour: up to six ~8-10 sim-s read cycles before a ~30 s
    # grasp — the 60 s desk cap timed out MID-TOUR on every non-trivial
    # episode of the first acceptance probe (run 20260811-161222-dda648);
    # the observed successful episode closed at t_end 49.2 s WITH the
    # target found at the fourth candidate
    assert tier_budgets("T2") == (T2_EPISODE_TIMEOUT_S, T2_PER_EPISODE_BUDGET_S)
    assert T2_EPISODE_TIMEOUT_S >= 6 * 10 + 30 + 30
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

    # CON-5: this runner-internal relaunch offset must not leak in from a
    # developer shell and renumber the initial launch.
    monkeypatch.setenv("AISLE_EPISODE_BASE", "999")

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
    episode_bases = []
    stderr_logs = []

    def fake_popen(cmd, cwd=None, env=None, **kwargs):
        # subprocess.run (the git calls) rides the same Popen; only the
        # dora spawns are under test
        if cmd[0] != "dora":
            proc = FakeProc()
            proc.args = cmd
            return proc
        spawns.append(env["AISLE_SEEDS"])
        episode_bases.append(env.get("AISLE_EPISODE_BASE"))
        # issue #183: the stderr sink is opened per launch in "w" mode
        stderr_logs.append(Path(kwargs["stderr"].name).name)
        results = Path(env["AISLE_RESULTS"])
        with open(results, "a") as f:
            if len(spawns) == 1:  # first launch: seed 0 lands, then wedges
                f.write('{"episode": 0, "seed": 0, "status": "success", "success": true}\n')
            else:  # relaunch: every remaining seed lands
                # the real client offsets by AISLE_EPISODE_BASE — the fixture
                # must too, or the test's own data contains the duplicate
                # indices this PR exists to prevent (PR #177 review)
                base = int(env["AISLE_EPISODE_BASE"])
                for i, s in enumerate(env["AISLE_SEEDS"].split(",")):
                    f.write(
                        f'{{"episode": {base + i}, "seed": {s}, '
                        f'"status": "success", "success": true}}\n'
                    )
        proc = FakeProc(alive=len(spawns) == 1)
        proc.args = cmd
        return proc

    monkeypatch.setattr(ro, "time", FakeClock())
    monkeypatch.setattr(ro.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        ro,
        "run_gates",
        lambda *a, **k: {
            "ok": True,
            "env_hash": "x",
            "sim_extra": "sim",
            "sim_backend": "metal",
            "sim_device": "mps",
        },
    )
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
    # issue #160 item 5: the relaunched client continues the RUN-GLOBAL
    # numbering (episode 0 succeeded + episode 1 clamped -> next is 2), so
    # goal_ids never repeat and fidelity.load_sidecar's duplicate refusal
    # cannot void a relaunched A7/both run.
    #
    # The FIRST launch carries no offset at all: AISLE_EPISODE_BASE is in
    # SCRUBBED_ENV, so the ambient 999 set above is stripped and the client
    # falls back to its documented default of 0. `None` here is therefore
    # the assertion that the ambient value did NOT survive (PR #177
    # review: scrubbing also covers the fleet and h4 paths, which an
    # explicit set inside rollout() did not).
    assert episode_bases == [None, "2"]
    # issue #183: each launch gets its OWN stderr sink. Popen opens these in
    # "w" mode, so one shared path meant the relaunch truncated the stderr
    # of the launch that had just wedged — deleting the only file that
    # explains why the clamp fired. Same isolation traces/relaunch-N/ has.
    assert stderr_logs == ["dora.stderr.log", "dora.stderr.relaunch-1.log"], stderr_logs
    assert len(set(stderr_logs)) == len(stderr_logs), "two launches shared one stderr sink"
    # and they really are on disk, not just named apart
    run_dir = root / "runs" / "clamp"
    for name in stderr_logs:
        assert (run_dir / name).exists(), f"{name} was never opened"
    # the PROPERTY, not just the env var: no two attempts in the run share
    # an episode index, which is what keeps goal_ids unambiguous
    indices = [e["episode"] for e in report["episodes"]]
    assert len(indices) == len(set(indices)), indices
    assert report["failures"] == {"wall_clamp": 1}
    clamped = [e for e in report["episodes"] if e.get("failure") == "wall_clamp"]
    assert [e["seed"] for e in clamped] == [1]  # the wedged seed, recorded
    assert report["pass1"] == pytest.approx(2 / 3)  # remaining seeds still scored
    manifest = json.loads((root / "runs" / "clamp" / "manifest.json").read_text())
    assert manifest["wall_clamped"] == [1] and manifest["relaunches"] == 1


def test_relaunch_reaps_orphans_and_isolates_trace_dirs(tmp_path, monkeypatch):
    """PR #58 review P1s: (a) stale dora nodes from the killed launch are
    CONCURRENT WRITERS (dora-rs/dora#2856) — orphans must be reaped
    between terminate and respawn; (b) the relaunch recorder truncates
    the first launch's Arrow/video files (pa.ipc.new_stream / imageio
    open write-mode) — each relaunch gets its own instrumented graph
    pointing at a fresh relaunch-N trace dir (HAR-4: prior evidence
    survives); (c) consecutive wedges each add build grace to the
    deadline, so every seed still gets a record instead of the tail
    being cut by the original budget."""
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
        grow = None  # trace file the live launch keeps appending to

        def monotonic(self):
            return self.t

        def time(self):
            return self.t

        def sleep(self, s):
            self.t += s
            if self.grow is not None:
                # the W/S2 signature: traces GROW while the episode wedges
                with open(self.grow, "ab") as f:
                    f.write(b"x")

    clock = FakeClock()

    class FakeProc:
        pid = 2**22
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

    events = []

    def fake_popen(cmd, cwd=None, env=None, **kwargs):
        if cmd[0] != "dora":
            proc = FakeProc()
            proc.args = cmd
            return proc
        graph_doc = yaml.safe_load(Path(cmd[2]).read_text())
        recorder = next(n for n in graph_doc["nodes"] if n["id"] == "trace-recorder")
        events.append(("spawn", env["AISLE_SEEDS"], recorder["env"]["AISLE_TRACE_DIR"]))
        launches = len([e for e in events if e[0] == "spawn"])
        if launches == 1:
            # launch 1's traces grow while it wedges (the whole reason the
            # stall detector cannot catch a wedged episode) and its bytes
            # REMAIN after the kill: the stall watcher must key its
            # pre-data grace on the CURRENT launch's dir, or the relaunch
            # build (no new bytes for >STALL_S while the prior total sits
            # nonzero) is falsely stall-killed (PR #58 self-review)
            tdir = Path(recorder["env"]["AISLE_TRACE_DIR"])
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "topic.arrow").write_bytes(b"x" * 64)
            clock.grow = tdir / "topic.arrow"
        if launches == 3:  # third launch: the remaining seed lands
            with open(env["AISLE_RESULTS"], "a") as f:
                f.write('{"episode": 0, "seed": 2, "status": "success", "success": true}\n')
        proc = FakeProc(alive=launches < 3)  # first two launches wedge
        proc.args = cmd
        return proc

    monkeypatch.setattr(ro, "time", clock)
    monkeypatch.setattr(ro.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        ro,
        "run_gates",
        lambda *a, **k: {
            "ok": True,
            "env_hash": "x",
            "sim_extra": "sim",
            "sim_backend": "metal",
            "sim_device": "mps",
        },
    )

    def fake_reap(*a, **k):
        events.append(("reap",))
        clock.grow = None  # the killed launch's writers stop with the reap

    monkeypatch.setattr(ro, "reap_orphans", fake_reap)
    report = ro.rollout(
        root=root,
        graph=root / "graphs" / "g.yaml",
        tier="T0",
        episodes=3,
        seeds=[0, 1, 2],
        reset_mode="teleport",
        verifier="oracle",
        run_id="wedges",
        branch="test",
        no_idea_gate=True,
        env_baseline="local",
    )
    spawn_dirs = [e[2] for e in events if e[0] == "spawn"]
    assert [e[1] for e in events if e[0] == "spawn"] == ["0,1,2", "1,2", "2"]
    assert len(set(spawn_dirs)) == 3  # per-launch trace dirs: no truncation
    assert "relaunch-1" in spawn_dirs[1] and "relaunch-2" in spawn_dirs[2]
    # a reap sits between every terminate and the next spawn
    kinds = [e[0] for e in events]
    assert kinds[:5] == ["spawn", "reap", "spawn", "reap", "spawn"]
    # every seed recorded despite two consecutive wedges (deadline grew)
    assert report["ok"] is True
    assert report["failures"] == {"wall_clamp": 2}
    assert sorted(e["seed"] for e in report["episodes"]) == [0, 1, 2]


def test_await_realistic_sidecar_counts_distinct_goal_ids(tmp_path):
    """Issue #160 item 5 (PR #168 review): the sidecar wait counted LINES,
    so duplicate goal_ids (the relaunch-numbering bug, or any re-judge)
    could mask a missing episode — three lines with two distinct goals
    must NOT satisfy expected=3. Malformed lines count for nothing."""
    from aisle.harness.rollout import await_realistic_sidecar

    sidecar = tmp_path / "verifier_stages.jsonl"
    sidecar.write_text(
        '{"goal_id": "ep-0000", "verdict": true}\n'
        '{"goal_id": "ep-0000", "verdict": false}\n'  # duplicate
        '{"goal_id": "ep-0001", "verdict": true}\n'
        "not json at all\n"
    )
    assert await_realistic_sidecar(tmp_path, expected=3, timeout_s=0.0) == 2
    assert await_realistic_sidecar(tmp_path, expected=2, timeout_s=0.0) == 2
    # ...and with a real timeout, so the IN-LOOP check runs (PR #177
    # review: timeout_s=0.0 never enters the wait loop, so the two asserts
    # above pass even against the line-counting bug this fix removes)
    assert await_realistic_sidecar(tmp_path, expected=2, timeout_s=5.0) == 2
    assert await_realistic_sidecar(tmp_path, expected=3, timeout_s=1.0) == 2


def test_await_realistic_sidecar_missing_file_is_zero(tmp_path):
    from aisle.harness.rollout import await_realistic_sidecar

    assert await_realistic_sidecar(tmp_path, expected=1, timeout_s=0.0) == 0


def test_a7_wall_budget_covers_full_sim_episode_plus_judge():
    """Issue #160 item 6 (PR #168 review): in A7 nothing ends an episode
    early — the reset/goal are downstream of the verifier's own verdict —
    so EVERY episode runs the full sim budget and is judged at expiry. The
    ADR-23 per-episode wall clamp must exceed full-sim-budget wall time at
    a pessimistic rtf plus the judge, or healthy A7 runs trip the clamp
    and then lose their VER-6 comparison to item 5's refusal."""
    from aisle.harness.rollout import (
        A7_JUDGE_BUDGET_S,
        A7_WALL_PER_SIM,
        EPISODE_TIMEOUT_S,
        PER_EPISODE_BUDGET_S,
        a7_per_episode_budget_s,
        tier_budgets,
    )

    # desk tier: the tier budget (150 s) is BELOW a full 60-sim-s episode
    # at pessimistic rtf + judge — A7 must raise it
    raised = a7_per_episode_budget_s(EPISODE_TIMEOUT_S, PER_EPISODE_BUDGET_S)
    assert raised == EPISODE_TIMEOUT_S * A7_WALL_PER_SIM + A7_JUDGE_BUDGET_S
    assert raised > PER_EPISODE_BUDGET_S
    # NOTE: the pure helper no-ops on retail (600*3+30 = 1830 < 2100), which
    # is exactly why resolve_budgets REFUSES that combination rather than
    # returning this number — see the test below.
    retail_timeout, retail_budget = tier_budgets("S1")
    assert a7_per_episode_budget_s(retail_timeout, retail_budget) == retail_budget


@pytest.mark.parametrize("tier", ["S1", "S2", "S3"])
def test_retail_a7_is_refused_not_silently_clamped(tier):
    """PR #177 review: A7_WALL_PER_SIM encodes a DESK rtf. At the retail rtf
    documented in this module (~101.5 sim s in ~25 wall min) a full 600 sim-s
    A7 episode costs ~8900 wall s against a 2100 s clamp, so every episode
    would clamp at ~24%, relaunch, and clamp again — a scored 0.0 dressed up
    as a budget. Refuse instead."""
    from aisle.harness.rollout import resolve_budgets, tier_budgets

    with pytest.raises(ValueError, match="no measured wall budget"):
        resolve_budgets(tier, "realistic")
    # the sidecar mode never changes control flow, so it stays available
    assert resolve_budgets(tier, "both") == tier_budgets(tier)
    assert resolve_budgets(tier, "oracle") == tier_budgets(tier)


def test_the_verifier_selects_the_budget_not_just_the_tier():
    """PR #177 review: the arithmetic above was tested, the WIRING was not
    — deleting the A7 branch at the rollout() call site left the whole unit
    suite green, because every rollout() test runs --verifier oracle."""
    from aisle.harness.rollout import PER_EPISODE_BUDGET_S, resolve_budgets, tier_budgets

    assert resolve_budgets("T0", "oracle") == tier_budgets("T0")
    assert resolve_budgets("T0", "both") == tier_budgets("T0")
    a7_timeout, a7_budget = resolve_budgets("T0", "realistic")
    assert (a7_timeout, a7_budget) != tier_budgets("T0")
    assert a7_budget > PER_EPISODE_BUDGET_S  # A7 episodes always run to expiry


def test_a7_budget_covers_the_judge_not_just_the_sim():
    """The judge term is the half a pure `wall_per_sim * timeout` formula
    would silently drop (PR #177 review: setting A7_JUDGE_BUDGET_S = 0 left
    the earlier test green)."""
    from aisle.harness.rollout import (
        A7_JUDGE_BUDGET_S,
        A7_WALL_PER_SIM,
        EPISODE_TIMEOUT_S,
        resolve_budgets,
    )

    assert A7_JUDGE_BUDGET_S > 0, "the judge must have real headroom, not zero"
    _, a7_budget = resolve_budgets("T0", "realistic")
    # strictly MORE than the sim time alone: the 3-5 s judge plus drain
    assert a7_budget >= EPISODE_TIMEOUT_S * A7_WALL_PER_SIM + 10


class TestEpisodeBaseConfig:
    """ADR-23 run-global numbering offset (PR #178 review): the runner
    always sets AISLE_EPISODE_BASE, but the documented dev path
    `dora run graphs/expert_t0.yaml --uv` does not, so the client must
    refuse junk loudly instead of dying on an uncaught ValueError."""

    def test_absent_defaults_to_zero(self):
        from aisle.harness.rollout_client import parse_episode_base

        assert parse_episode_base({}) == 0

    def test_runner_supplied_offset_is_read(self):
        from aisle.harness.rollout_client import parse_episode_base

        assert parse_episode_base({"AISLE_EPISODE_BASE": "2"}) == 2
        assert parse_episode_base({"AISLE_EPISODE_BASE": " 7 "}) == 7

    @pytest.mark.parametrize("raw", ["x", "1.5", "", "0x10", "1e3"])
    def test_malformed_refuses_loudly(self, raw):
        from aisle.harness.rollout_client import parse_episode_base

        with pytest.raises(SystemExit, match="AISLE_EPISODE_BASE"):
            parse_episode_base({"AISLE_EPISODE_BASE": raw})

    def test_negative_offset_refuses_rather_than_aliasing(self):
        """A negative base mints `ep--005` and aliases earlier episodes —
        the exact collision the offset exists to prevent."""
        from aisle.harness.rollout_client import parse_episode_base

        with pytest.raises(SystemExit, match="AISLE_EPISODE_BASE"):
            parse_episode_base({"AISLE_EPISODE_BASE": "-5"})


def test_dora_stderr_log_is_per_launch(tmp_path):
    """HAR-4 (issue #183): evidence must survive. `Popen` opens this sink in
    write mode, so every launch needs its own path — a shared one meant each
    wall-clamp relaunch (ADR-23) truncated the wedged launch's diagnostics.

    Launch 1 keeps the bare name so existing docs and habits still find it."""
    from aisle.harness.rollout import dora_stderr_log

    run = tmp_path / "run"
    assert dora_stderr_log(run).name == "dora.stderr.log"
    assert dora_stderr_log(run, 0).name == "dora.stderr.log"
    names = [dora_stderr_log(run, n).name for n in range(4)]
    assert len(set(names)) == 4, names
    # all at the run root, beside each other, and sortable
    assert all(dora_stderr_log(run, n).parent == run for n in range(4))
    assert names[1:] == [f"dora.stderr.relaunch-{n}.log" for n in (1, 2, 3)]
