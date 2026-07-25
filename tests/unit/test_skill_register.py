"""T18: skill registration (design doc §8.4 item 1, §3 rule 3, §9.2;
CAP-5/CAP-7 curated-core amendment) — `harness skill register` validates the
skill (CAP-1..3), STAGES the candidate into the registry so its own eval
can discover it (the PR #30 blocker), runs the shipped eval suite bound
to the candidate, writes the evalcard (CAP-6), and rolls back exactly on
any failure. Governance: curated ids refused from the single-sourced
Class-C list regardless of file state. Rollouts are injected so these
tests never touch sim (CON-12); CON-8 for the CLI contract; CON-5 via
the injected clock.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

from aisle.harness.skill import (  # noqa: E402
    RegistrationError,
    curated_ids,
    load_skill,
    register_skill,
)


def _write_skill(
    root: Path,
    name: str = "pour-arc",
    *,
    manifest_extra: dict | None = None,
    eval_extra: dict | None = None,
    omit_eval: bool = False,
    graph_nodes: list | None = None,
) -> Path:
    """A minimal, schema-valid node skill with a shipped eval suite whose
    graph USES the candidate (a node with the skill's id)."""
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": name,
        "kind": "node",
        "provides": ["pour_control"],
        "requires": ["object_pose"],
        "inputs": {"object_pose": {"schema": "pose7d_f32", "rate_hz": 30}},
        "outputs": {"joint_cmd": {"schema": "jointvec_f32", "latency_class": "soft_rt"}},
        "embodiment": {"arm": ["franka"], "gripper": "parallel"},
        "safety_class": "motion",
        "eval": None,  # written by registration
        "origin": "agent-authored",
        "source": f"skills/{name}/node.py",
        **(manifest_extra or {}),
    }
    (d / "skill.yaml").write_text(yaml.safe_dump(manifest))
    (d / "node.py").write_text("def main():\n    pass\n")
    if not omit_eval:
        eval_cfg = {
            "suite": f"{name}_eval_v0",
            "graph": f"skills/{name}/eval_graph.yaml",
            "tier": "T1",
            "episodes": 4,
            "seeds": "0..3",
            "embodiment": "franka",
            "min_pass_rate": 0.5,
            **(eval_extra or {}),
        }
        (d / "eval.yaml").write_text(yaml.safe_dump(eval_cfg))
        nodes = graph_nodes if graph_nodes is not None else [{"id": name, "path": "node.py"}]
        (d / "eval_graph.yaml").write_text(yaml.safe_dump({"nodes": nodes}))
    return d


def _registry(root: Path) -> Path:
    """A registry root with the REAL schema files (incl. the curated-core
    list) and one curated manifest."""
    (root / "registry" / "manifests").mkdir(parents=True)
    (root / "registry" / "schema").mkdir(parents=True)
    for f in (REPO_ROOT / "registry" / "schema").glob("*"):
        (root / "registry" / "schema" / f.name).write_text(f.read_text())
    core = (REPO_ROOT / "registry" / "manifests" / "oracle-pose.yaml").read_text()
    (root / "registry" / "manifests" / "oracle-pose.yaml").write_text(core)
    return root


def _ok_rollout(report=None):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return report or {"ok": True, "pass1": 0.75, "pass8": 0.75, "episodes": [{}] * 4}

    fake.calls = calls
    return fake


def test_register_writes_evalcard_and_installs_manifest(tmp_path):
    """§8.4: validate → stage → eval → evalcard → install."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    fake = _ok_rollout()
    result = register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    assert result["ok"] is True and result["pass_rate"] == 0.75
    installed = yaml.safe_load((root / "registry" / "manifests" / "pour-arc.yaml").read_text())
    assert installed["eval"] == {
        "suite": "pour-arc_eval_v0",
        "pass_rate": 0.75,
        "last_run": "2026-07-25",
    }
    assert fake.calls and fake.calls[0]["tier"] == "T1" and fake.calls[0]["episodes"] == 4


def test_candidate_is_staged_and_discoverable_during_its_own_eval(tmp_path):
    """THE PR #30 blocker: during the eval rollout the candidate manifest
    must be IN the registry (with a provisional evalcard so CAP-6's
    motion gate passes) — proven with the REAL validator: unstaged, the
    eval graph gets MANIFEST_MISSING; at eval time, it validates."""
    from aisle.harness.validate import validate

    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    graph = skill_dir / "eval_graph.yaml"

    # unstaged: the real validator cannot discover the candidate
    before = validate(graph, root, "franka", allow_unproven=False)
    assert any(e["code"] == "MANIFEST_MISSING" for e in before["errors"]), before

    seen = {}

    def probing_rollout(**kwargs):
        staged = yaml.safe_load((root / "registry" / "manifests" / "pour-arc.yaml").read_text())
        seen["staged"] = staged
        report = validate(graph, root, "franka", allow_unproven=False)
        seen["missing_at_eval"] = [e for e in report["errors"] if e["code"] == "MANIFEST_MISSING"]
        return {"ok": True, "pass1": 1.0, "episodes": [{}]}

    register_skill(skill_dir, root, run_rollout=probing_rollout, now="2026-07-25")
    assert seen["missing_at_eval"] == []  # discoverable during its own eval
    assert seen["staged"]["eval"]["last_run"] == "provisional-2026-07-25"
    assert "(provisional)" in seen["staged"]["eval"]["suite"]  # CAP-6 pass, labelled


def test_update_evaluates_the_new_candidate_not_the_old(tmp_path):
    """PR #30: re-registering evaluates the STAGED new manifest — the old
    installed version must not shadow it during the eval."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-24")
    # v2 changes provides; the rollout must see v2 staged
    _write_skill(root, manifest_extra={"provides": ["pour_control_v2"]})
    seen = {}

    def probing_rollout(**kwargs):
        staged = yaml.safe_load((root / "registry" / "manifests" / "pour-arc.yaml").read_text())
        seen["provides"] = staged["provides"]
        return {"ok": True, "pass1": 1.0, "episodes": [{}]}

    register_skill(skill_dir, root, run_rollout=probing_rollout, now="2026-07-25")
    assert seen["provides"] == ["pour_control_v2"]


def test_failed_eval_rolls_back_exactly(tmp_path):
    """Any failure restores the registry byte-for-byte: a fresh candidate
    vanishes; an update keeps its PRIOR manifest."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    bad = _ok_rollout(report={"ok": False, "refused": {"gate": "validate"}})
    with pytest.raises(RegistrationError, match="eval run failed"):
        register_skill(skill_dir, root, run_rollout=bad, now="2026-07-25")
    assert not (root / "registry" / "manifests" / "pour-arc.yaml").exists()

    # update case: v1 installs, v2's eval fails below threshold -> v1 stays
    register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-24")
    v1 = (root / "registry" / "manifests" / "pour-arc.yaml").read_text()
    _write_skill(root, eval_extra={"min_pass_rate": 0.99})
    with pytest.raises(RegistrationError, match="pass_rate"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")
    assert (root / "registry" / "manifests" / "pour-arc.yaml").read_text() == v1


def test_eval_graph_must_use_the_candidate(tmp_path):
    """PR #30: an unrelated green graph cannot mint the evalcard — the
    eval graph must contain a node with the candidate's id."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, graph_nodes=[{"id": "oracle-pose", "path": "x.py"}])
    fake = _ok_rollout()
    with pytest.raises(RegistrationError, match="does not use the candidate"):
        register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    assert not fake.calls


def test_curated_id_refused_even_when_core_file_deleted(tmp_path):
    """CAP-7: core protection comes from the Class-C curated list, not
    mutable file presence — deleting the core manifest opens nothing."""
    root = _registry(tmp_path)
    (root / "registry" / "manifests" / "oracle-pose.yaml").unlink()
    skill_dir = _write_skill(root, name="oracle-pose")
    with pytest.raises(RegistrationError, match="CURATED core"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")
    assert "oracle-pose" in curated_ids(root)


def test_register_refuses_non_agent_authored(tmp_path):
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, manifest_extra={"origin": "hub"})
    with pytest.raises(RegistrationError, match="origin"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")


def test_register_refuses_schema_invalid_before_any_eval(tmp_path):
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, manifest_extra={"safety_class": "chaotic"})
    fake = _ok_rollout()
    with pytest.raises(RegistrationError, match="schema"):
        register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    assert not fake.calls


def test_register_refuses_missing_eval_suite(tmp_path):
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, omit_eval=True)
    with pytest.raises(RegistrationError, match="eval"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")


def test_malformed_eval_fields_are_registration_errors(tmp_path):
    """PR #30 (CON-8): bad seed specs and non-numeric fields refuse
    cleanly — never a traceback."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, eval_extra={"seeds": "abc"})
    with pytest.raises(RegistrationError, match="invalid"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")
    skill_dir = _write_skill(root, name="pour-arc2", eval_extra={"episodes": "many"})
    with pytest.raises(RegistrationError, match="invalid"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")
    (root / "skills" / "pour-arc3").mkdir(parents=True)
    (root / "skills" / "pour-arc3" / "skill.yaml").write_text("{bad: yaml: [")
    with pytest.raises(RegistrationError, match="YAML"):
        register_skill(root / "skills" / "pour-arc3", root, run_rollout=_ok_rollout(), now="x")


def test_same_day_retries_get_distinct_run_ids(tmp_path):
    """PR #30: the evaluate-fix loop must allow a second attempt the same
    day — default run ids are uniquified."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    fake = _ok_rollout()
    r1 = register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    r2 = register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    assert r1["eval_run_id"] != r2["eval_run_id"]
    assert fake.calls[0]["run_id"] != fake.calls[1]["run_id"]


def test_load_skill_reads_manifest_and_eval(tmp_path):
    root = _registry(tmp_path)
    skill = load_skill(_write_skill(root))
    assert skill.manifest["id"] == "pour-arc"
    assert skill.eval_cfg["min_pass_rate"] == 0.5


def test_cli_surface_parses():
    """CON-8: the CLI subcommand exists with the documented flags."""
    from aisle.harness.cli import build_parser

    args = build_parser().parse_args(["skill", "register", "skills/pour-arc"])
    assert args.command == "skill" and args.skill_command == "register"


def test_cli_register_json_contract(tmp_path):
    """CON-8 end to end: JSON on stdout, exit 1 on a refused registration
    (malformed seeds here — no rollout machinery touched)."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, eval_extra={"seeds": "abc"})
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "skill",
            "register",
            str(skill_dir),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["ok"] is False and "invalid" in out["error"]
