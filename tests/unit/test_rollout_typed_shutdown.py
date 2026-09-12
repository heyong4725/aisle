"""MON-13: episode completion must leave time for typed host postflight."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("host_done_at, expected_stop", [(120, 120), (1000, 200)])
def test_typed_postflight_uses_remaining_run_budget(
    tmp_path, monkeypatch, host_done_at, expected_stop
):
    """MON-13/HAR-1: delayed host completion survives quiet time but never the run deadline."""
    from aisle.harness import rollout as ro
    from aisle.harness import typed_graph_audit, typed_graph_stage

    graph = tmp_path / "graphs/g.yaml"
    graph.parent.mkdir()
    graph.write_text("nodes: []\n")
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness/budget.toml").write_text("[campaign]\ntokens=1\nepisodes=10\nwall_h=1\n")

    class Clock:
        now = 0

        def monotonic(self):
            return self.now

        def sleep(self, duration):
            self.now += duration

    clock = Clock()
    process = SimpleNamespace(poll=lambda: 0 if clock.now >= host_done_at else None)
    stopped = []

    def spawn(graph, run_dir, environment, **kwargs):
        Path(environment["AISLE_RESULTS"]).write_text(
            '{"episode":0,"seed":7,"status":"success","t_end":1.0,"retries":0}\n'
        )
        return process

    stage = (tmp_path / "stage", {"immutable_id": "stage"})
    monkeypatch.setattr(
        typed_graph_stage, "select_rollout_stage", lambda *a, **k: (stage, tmp_path)
    )
    monkeypatch.setattr(ro, "instrumented_graph", lambda *a, **k: graph)
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
    original_run = ro.subprocess.run
    monkeypatch.setattr(
        ro.subprocess,
        "run",
        lambda command, *a, **k: (
            SimpleNamespace(stdout="test", returncode=0)
            if command[0] == "git"
            else original_run(command, *a, **k)
        ),
    )
    monkeypatch.setattr(ro, "time", clock)
    monkeypatch.setattr(ro, "_spawn_dora", spawn)
    monkeypatch.setattr(ro, "_terminate", lambda p: stopped.append(clock.now))
    monkeypatch.setattr(ro, "reap_orphans", lambda *a, **k: None)
    monkeypatch.setattr(
        typed_graph_audit,
        "audit_graph_stage",
        lambda *a, **k: {
            "ok": clock.now >= host_done_at,
            "errors": [] if clock.now >= host_done_at else ["unfinished"],
        },
    )
    monkeypatch.setattr(typed_graph_audit, "retain_graph_stage", lambda *a, **k: {"ok": True})
    report = ro.rollout(
        root=tmp_path,
        graph=graph,
        tier="T1",
        episodes=1,
        seeds=[7],
        reset_mode="teleport",
        verifier="oracle",
        run_id="shutdown",
        branch="test",
        no_idea_gate=True,
        env_baseline="local",
        timeout_s=200,
        typed_stage_factory=lambda *a, **k: None,
    )
    assert stopped == [expected_stop]
    assert report["ok"] is (host_done_at < 200)
