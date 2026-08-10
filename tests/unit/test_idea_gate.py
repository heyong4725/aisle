"""SPEC 070 idea tree and rollout gates (HAR-2, HAR-7, HAR-8) — no dora,
no sim (CON-12)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.ideas import close_idea, log_idea, open_ideas
from aisle.harness.rollout import run_gates

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_rollout_cli_exposes_the_attested_simulation_extra():
    """HAR-1, CON-5: the public rollout path exposes the exact dependency
    selection; CUDA cannot be activated by an ambient hardware probe."""
    from aisle.harness.cli import build_parser

    base = [
        "rollout",
        "--graph",
        "graphs/expert_t0.yaml",
        "--episodes",
        "1",
        "--seeds",
        "0",
    ]
    assert build_parser().parse_args(base).sim_extra == "sim"
    assert build_parser().parse_args([*base, "--sim-extra", "cuda"]).sim_extra == "cuda"


def test_log_appends_jsonl_with_monotonic_ids(tmp_path):
    """HAR-7: `report log` appends JSONL entries with branch-monotonic ids,
    injected timestamp and git sha (CON-5)."""
    first = log_idea(tmp_path, "feat/x", "try wider grasp", "t0", "sha0", expect="+10pp on T1")
    second = log_idea(tmp_path, "feat/x", "raise gains", "t1", "sha1", parent=first["id"])
    assert (first["id"], second["id"]) == ("I1", "I2")
    lines = (tmp_path / "runs" / "ideas" / "feat__x.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["I1", "I2"]
    assert json.loads(lines[0])["expect"] == "+10pp on T1"
    assert json.loads(lines[1])["parent"] == "I1"


def test_open_iff_logged_and_not_closed(tmp_path):
    """HAR-8: an idea is OPEN if logged and not closed; closing removes it
    from the open set without rewriting history (append-only)."""
    log_idea(tmp_path, "b", "idea one", "t0", "sha")
    log_idea(tmp_path, "b", "idea two", "t1", "sha")
    assert [e["id"] for e in open_ideas(tmp_path, "b")] == ["I1", "I2"]
    close_idea(tmp_path, "b", "I1", "observed flat", "flat", "t2")
    assert [e["id"] for e in open_ideas(tmp_path, "b")] == ["I2"]
    lines = (tmp_path / "runs" / "ideas" / "b.jsonl").read_text().splitlines()
    assert len(lines) == 3  # append-only: log, log, close


def test_close_requires_an_open_idea_and_valid_verdict(tmp_path):
    log_idea(tmp_path, "b", "idea", "t0", "sha")
    with pytest.raises(ValueError, match="no open idea"):
        close_idea(tmp_path, "b", "I9", "x", "up", "t1")
    with pytest.raises(ValueError, match="verdict"):
        close_idea(tmp_path, "b", "I1", "x", "sideways", "t1")
    close_idea(tmp_path, "b", "I1", "went flat", "flat", "t1")
    # PR #11 review: a second, contradictory close must be refused
    with pytest.raises(ValueError, match="no open idea"):
        close_idea(tmp_path, "b", "I1", "actually up", "up", "t2")


def _fake_root(
    tmp_path: Path, hash_ok: bool = True, episodes_ceiling: int = 500, wall_h: float = 40.0
) -> Path:
    """A minimal root that passes/fails the env-hash gate deterministically;
    the REAL registry rides along (symlink) so the validation gate can pass
    and the idea gate is what decides. Carries a campaign budget.toml
    (ADR-21) with configurable ceilings."""
    (tmp_path / "registry").symlink_to(REPO_ROOT / "registry")
    # PATH_MANIFEST_MISMATCH (#62) + SOURCE_INVALID containment (#35/PR
    # #63) resolve manifest sources under the root — a symlinked src/
    # RESOLVES OUTSIDE the fake root and is now (correctly) refused, so
    # the fixture carries a real copy
    shutil.copytree(
        REPO_ROOT / "src",
        tmp_path / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # ...and its own graphs/: PATH_MANIFEST_MISMATCH is an IDENTITY check
    # (graph path file == root source file), so a repo graph can never
    # validate against a foreign root — the gate tests validate the fake
    # root's own copy
    shutil.copytree(REPO_ROOT / "graphs", tmp_path / "graphs")
    (tmp_path / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "env_hash.py").write_text(
        'import json, sys\nprint(json.dumps({"ok": '
        + ("True" if hash_ok else "False")
        + ', "env_hash": "h"}))\nsys.exit(0 if '
        + ("True" if hash_ok else "False")
        + " else 1)\n"
    )
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / "budget.toml").write_text(
        f"[campaign]\ntokens = 5000000\nepisodes = {episodes_ceiling}\nwall_h = {wall_h}\n"
    )
    return tmp_path


def test_gate_refuses_on_env_hash_mismatch(tmp_path):
    """HAR-2: rollout MUST refuse when tools/env_hash.py --check fails
    (CON-7 frozen-set drift)."""
    root = _fake_root(tmp_path, hash_ok=False)
    result = run_gates(
        root, root / "graphs" / "expert_t0.yaml", "b", no_idea_gate=True, env_baseline="local"
    )
    assert result["ok"] is False and result["gate"] == "env_hash"


def test_gate_attests_the_selected_sim_extra_and_backend(tmp_path, monkeypatch):
    """HAR-2, HAR-4, CON-5: a CUDA rollout checks the CUDA lock selection
    and carries its resolved backend/device identity out of the gate."""
    from aisle.harness import rollout as rollout_module

    root = _fake_root(tmp_path, hash_ok=True)
    argv_path = root / "hash-argv.json"
    (root / "tools" / "env_hash.py").write_text(
        "import json, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        'print(json.dumps({"ok": True, "env_hash": "h"}))\n'
    )
    monkeypatch.setattr(
        rollout_module,
        "resolve_sim_identity",
        lambda extra: {
            "ok": True,
            "sim_extra": extra,
            "sim_backend": "cuda",
            "sim_device": "NVIDIA Test GPU",
        },
    )

    result = rollout_module.run_gates(
        root,
        root / "graphs" / "expert_t0.yaml",
        "b",
        no_idea_gate=True,
        env_baseline="local",
        sim_extra="cuda",
    )

    assert result["ok"] is True
    assert (result["sim_extra"], result["sim_backend"], result["sim_device"]) == (
        "cuda",
        "cuda",
        "NVIDIA Test GPU",
    )
    args = json.loads(argv_path.read_text())
    assert args[args.index("--extras") + 1] == "cuda"


def test_gate_fails_closed_when_requested_backend_is_unavailable(tmp_path, monkeypatch):
    """HAR-2, CON-5: an explicit CUDA run refuses before validation or
    launch when the selected backend cannot be honored; it never falls back."""
    from aisle.harness import rollout as rollout_module

    root = _fake_root(tmp_path, hash_ok=True)
    monkeypatch.setattr(
        rollout_module,
        "resolve_sim_identity",
        lambda extra: {
            "ok": False,
            "gate": "sim_backend",
            "detail": "CUDA device unavailable",
        },
    )

    result = rollout_module.run_gates(
        root,
        root / "graphs" / "expert_t0.yaml",
        "b",
        no_idea_gate=True,
        env_baseline="local",
        sim_extra="cuda",
    )

    assert result == {
        "ok": False,
        "gate": "sim_backend",
        "detail": "CUDA device unavailable",
    }


def test_gate_refuses_without_open_idea_and_bypass_is_recorded(tmp_path):
    """HAR-2/HAR-8: no OPEN idea on the branch refuses the launch; the
    humans-only --no-idea-gate bypass is surfaced so the manifest logs it."""
    from aisle.harness.validate import validate

    root = _fake_root(tmp_path, hash_ok=True)
    graph = root / "graphs" / "expert_t0.yaml"
    if not validate(graph, root, "franka", allow_unproven=False)["ok"]:
        pytest.skip("expert graph does not validate in this environment")
    refused = run_gates(root, graph, "b", no_idea_gate=False, env_baseline="local")
    assert refused["ok"] is False and refused["gate"] == "idea"
    log_idea(root, "b", "the campaign idea", "t0", "sha")
    passed = run_gates(root, graph, "b", no_idea_gate=False, env_baseline="local")
    assert passed["ok"] is True
    assert passed["idea"] == "I1" and passed["no_idea_gate"] is False
    bypass = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="local")
    assert bypass["ok"] is True and bypass["no_idea_gate"] is True


def test_report_cli_json_contract(tmp_path):
    """CON-8 + HAR-7: `harness report log/close` emit a single JSON object
    on stdout and exit 0 iff ok."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "report",
            "log",
            "--idea",
            "test idea",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    entry = json.loads(proc.stdout)
    assert entry["ok"] is True and entry["id"] == "I1" and entry["status"] == "open"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "report",
            "close",
            "--id",
            "I9",
            "--observed",
            "x",
            "--verdict",
            "up",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["ok"] is False


def test_reserve_budget_is_atomic_check_and_reserve(tmp_path):
    """ADR-21 round 3 (PR #24): budget spend is RESERVED before launch
    under the ledger lock — a second reservation past the ceiling refuses,
    and an unsettled reservation stays charged (crash accounting)."""
    from aisle.harness.rollout import budget_remaining, reserve_budget, settle_budget

    root = _fake_root(tmp_path, hash_ok=True, episodes_ceiling=10)
    first = reserve_budget(root, "r1", episodes=8)
    assert first["ok"] is True and first["entry"]
    # unsettled reservation charges the ceiling: 3 > 2 left -> refused
    refused = reserve_budget(root, "r2", episodes=3)
    assert refused["ok"] is False and refused["gate"] == "budget"
    assert "episode budget" in refused["detail"]
    # settling at the ACTUAL count (5 of 8 ran) releases the difference
    settle_budget(root, "r1", episodes=5, wall_s=60.0)
    assert budget_remaining(root)["episodes_left"] == 5
    assert reserve_budget(root, "r2", episodes=3)["ok"] is True


def test_wall_ceiling_refuses_reservation(tmp_path):
    from aisle.harness.rollout import reserve_budget, settle_budget

    root = _fake_root(tmp_path, hash_ok=True, wall_h=0.5)
    ok = reserve_budget(root, "r1", episodes=1)
    assert ok["ok"] is True
    settle_budget(root, "r1", episodes=1, wall_s=1900.0)  # > 0.5 h spent
    refused = reserve_budget(root, "r2", episodes=1)
    assert refused["ok"] is False and refused["gate"] == "budget"
    assert "wall-clock" in refused["detail"]


def test_ledger_chain_is_tamper_evident(tmp_path):
    """ADR-21 round 3: the ledger is hash-chained — editing any entry
    breaks verification, and manifests carry entry hashes for the audit."""
    from aisle.harness.rollout import reserve_budget, settle_budget, verify_ledger

    root = _fake_root(tmp_path, hash_ok=True)
    reserve_budget(root, "r1", episodes=2)
    settle_budget(root, "r1", episodes=2, wall_s=30.0)
    assert verify_ledger(root) is True
    path = root / "runs" / "campaign_ledger.jsonl"
    tampered = path.read_text().replace('"episodes": 2', '"episodes": 1', 1)
    path.write_text(tampered)
    assert verify_ledger(root) is False


def test_local_override_is_exempt_from_budget_refusal(tmp_path):
    """PR #24 P2: exhausted campaign budgets must NOT block local
    development runs — they neither charge nor consume the campaign."""
    from aisle.harness.rollout import reserve_budget, settle_budget

    root = _fake_root(tmp_path, hash_ok=True, episodes_ceiling=1)
    reserve_budget(root, "r1", episodes=1)
    settle_budget(root, "r1", episodes=1, wall_s=10.0)
    graph = root / "graphs" / "expert_t0.yaml"
    result = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="local", episodes=5)
    assert result["ok"] is True  # exhausted campaign, local run still allowed
    assert result["budget"]["episodes_left"] == 0  # ...and remaining is reported


def test_unknown_baseline_is_refused(tmp_path):
    """ADR-21 round 3 (PR #24): only the server-resolved 'origin/main' or
    the logged 'local' override are accepted — an agent cannot point the
    gate at HEAD or any ref it controls."""
    root = _fake_root(tmp_path, hash_ok=True)
    graph = root / "graphs" / "expert_t0.yaml"
    for ref in ("HEAD", "main", "refs/heads/feature", "origin/other"):
        result = run_gates(root, graph, "b", no_idea_gate=True, env_baseline=ref)
        assert result["ok"] is False and result["gate"] == "env_hash", ref
        assert "unknown baseline" in result["detail"]


def test_gates_record_the_env_baseline(tmp_path):
    """ADR-21: every gate result names the frozen-set baseline that
    validated it — 'local' (the dev override) is auditable in manifests."""
    root = _fake_root(tmp_path, hash_ok=True)
    graph = root / "graphs" / "expert_t0.yaml"
    result = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="local")
    assert result["ok"] is True and result["env_baseline"] == "local"
    assert result["env_baseline_oid"] is None  # no immutable identity claimed


def test_trusted_baseline_resolves_from_the_server_not_local_refs(tmp_path):
    """ADR-21 round 3 (PR #24): the trusted baseline is FETCHED from the
    remote at gate time and pinned by commit OID — moving the local
    remote-tracking ref (the reviewer's attack) changes nothing, and a
    root with no remote fails CLOSED."""
    import os as _os

    from aisle.harness.rollout import resolve_trusted_baseline

    env = {
        "PATH": _os.environ["PATH"],
        "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(cwd, *args):
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
        assert proc.returncode == 0, (args, proc.stderr)
        return proc.stdout.strip()

    server = tmp_path / "server.git"
    server.mkdir()
    git(server, "init", "-q", "--bare", "-b", "main")
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "f.txt").write_text("baseline\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "baseline")
    git(work, "remote", "add", "origin", str(server))
    git(work, "push", "-q", "origin", "main")
    server_oid = git(work, "rev-parse", "HEAD")

    oid, err = resolve_trusted_baseline(work)
    assert err is None and oid == server_oid

    # the attack: advance local work and MOVE the local remote-tracking ref
    (work / "f.txt").write_text("tampered\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "local tamper")
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    oid, err = resolve_trusted_baseline(work)
    assert err is None and oid == server_oid  # still the SERVER's head

    # fail closed without a remote
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    git(lonely, "init", "-q", "-b", "main")
    oid, err = resolve_trusted_baseline(lonely)
    assert oid is None and err is not None


def test_trusted_gate_refuses_on_dist_drift_and_missing_evidence(tmp_path, monkeypatch):
    """ADR-24 D2/D3 (HAR-2): trusted-baseline runs REFUSE when the
    self-verified checker reports a failed attestation (DIST_DRIFT) or
    emits no attestation evidence at all — record-by-convention is not a
    gate. Local runs record `attested` honestly without refusing."""
    from aisle.harness import rollout as ro

    root = _fake_root(tmp_path, hash_ok=True)
    graph = root / "graphs" / "expert_t0.yaml"
    monkeypatch.setattr(ro, "resolve_trusted_baseline", lambda r: ("deadbeef", None))

    def stub_env_hash(payload):
        (root / "tools" / "env_hash.py").write_text(
            f"import json\nprint(json.dumps({payload!r}))\n"
        )

    # failed attestation -> DIST_DRIFT refusal
    stub_env_hash(
        {
            "ok": True,
            "env_hash": "h",
            "dist": {"attested": False, "env_fingerprint": "fp", "problems": ["uv.lock diverges"]},
        }
    )
    refused = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="origin/main")
    assert refused["ok"] is False and refused["gate"] == "dist"
    assert "DIST_DRIFT" in refused["detail"] and "uv.lock diverges" in refused["detail"]

    # missing evidence entirely -> refusal too (stale checker at baseline)
    stub_env_hash({"ok": True, "env_hash": "h"})
    refused = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="origin/main")
    assert refused["ok"] is False and refused["gate"] == "dist"

    # attested trusted run passes and carries the fingerprint
    stub_env_hash(
        {
            "ok": True,
            "env_hash": "h",
            "dist": {"attested": True, "env_fingerprint": "fp123", "problems": []},
        }
    )
    passed = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="origin/main")
    assert passed["ok"] is True
    assert passed["env_fingerprint"] == "fp123" and passed["env_attested"] is True

    # local run with a failed attestation: records honestly, no refusal
    stub_env_hash(
        {
            "ok": True,
            "env_hash": "h",
            "dist": {"attested": False, "env_fingerprint": "fpX", "problems": ["drift"]},
        }
    )
    local = run_gates(root, graph, "b", no_idea_gate=True, env_baseline="local")
    assert local["ok"] is True
    assert local["env_attested"] is False and local["dist_problems"] == ["drift"]


def test_trusted_run_attestation_is_final_only_after_post_run_audit(tmp_path, monkeypatch):
    """ADR-24 D2 as hardened by the PR #69 review: rollout's manifest may
    mark a trusted run attested ONLY if the post-run audit (gate-time
    inventory, self-verified checker) also passes — a mid-session
    mutation flips env_attested to false with the audit recorded."""
    import json as _json

    from aisle.harness import rollout as ro

    root = _fake_root(tmp_path, hash_ok=True)
    graph = root / "graphs" / "expert_t0.yaml"
    monkeypatch.setattr(ro, "resolve_trusted_baseline", lambda r: ("deadbeef", None))
    monkeypatch.setattr(ro, "reap_orphans", lambda *a, **k: None)

    # fake trusted checker: gate PASSES with an inventory; the post-run
    # audit mode reports a mutation
    (root / "tools" / "env_hash.py").write_text(
        """
import json, sys
if "--verify-records" in sys.argv:
    print(json.dumps({"ok": False, "problems": ["numpy: f.py does not match its RECORD hash"]}))
else:
    print(json.dumps({
        "ok": True, "env_hash": "h",
        "dist": {"attested": True, "env_fingerprint": "fp", "problems": [],
                 "inventory": {"numpy": {"version": "1", "record_sha256": "r"}}},
    }))
"""
    )

    class FakeProc:
        pid = 2**22
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

    real_popen = ro.subprocess.Popen

    def fake_popen(cmd, cwd=None, env=None, **kwargs):
        if cmd and cmd[0] == "dora":
            proc = FakeProc()
            proc.args = cmd
            return proc
        # the stub env_hash.py (gate + post-run audit) runs for real
        return real_popen(cmd, cwd=cwd, env=env, **kwargs)

    monkeypatch.setattr(ro.subprocess, "Popen", fake_popen)
    report = ro.rollout(
        root=root,
        graph=graph,
        tier="T0",
        episodes=1,
        seeds=[0],
        reset_mode="teleport",
        verifier="oracle",
        run_id="postaudit",
        branch="test",
        no_idea_gate=True,
        env_baseline="origin/main",
    )
    manifest = _json.loads((root / "runs" / "postaudit" / "manifest.json").read_text())
    assert manifest["env_attested"] is False  # gate passed, audit failed
    assert manifest["post_run_audit"]["ok"] is False
    assert any("RECORD" in p for p in manifest["post_run_audit"]["problems"])
    assert (root / "runs" / "postaudit" / "gate_inventory.json").exists()  # evidence
    assert report["ok"] is False  # fake dora ran zero episodes (unrelated)


def test_rollout_scrubs_bringup_env():
    """ADR-25 (issue #71, CON-5): AISLE_STEP_WITHOUT_RESET never reaches a
    measured rollout's dora environment — ambient leakage would silently
    restore the pre-reset startup race with clean attestation fields."""
    from aisle.harness.rollout import scrub_bringup_env

    env = scrub_bringup_env({"AISLE_STEP_WITHOUT_RESET": "1", "AISLE_TIER": "S1"})
    assert "AISLE_STEP_WITHOUT_RESET" not in env
    assert env == {"AISLE_TIER": "S1"}


def test_rollout_scrubs_the_perception_rung():
    """TC-9: the rung MUST come from the graph, where the graph hash attests
    it, and never from the ambient environment. The bridge reads it via
    parse_bridge_config(os.environ), so an ambient AISLE_PERCEPTION=L1 would
    set the rung of a run whose graph declared none — and the validator, which
    sees only graph YAML, could never detect the divergence. Same hazard ADR-25
    wrote this scrub for."""
    from aisle.harness.rollout import scrub_bringup_env

    scrubbed = scrub_bringup_env(
        {"AISLE_PERCEPTION": "L1", "AISLE_STEP_WITHOUT_RESET": "1", "PATH": "/usr/bin"}
    )
    assert "AISLE_PERCEPTION" not in scrubbed
    assert "AISLE_STEP_WITHOUT_RESET" not in scrubbed
    assert scrubbed["PATH"] == "/usr/bin"
