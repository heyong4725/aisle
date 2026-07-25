"""T18: skill registration (design doc §8.4 item 1, §3 rule 3, §9.2) —
`harness skill register skills/<name>/` validates the skill's manifest
(CAP-1..3), runs its shipped eval suite, writes the evalcard
(CAP-6: motion-class skills are unusable without one), and installs the
manifest into the registry under the governance rules (origin
agent-authored, core ids protected). Rollouts are injected so these
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
) -> Path:
    """A minimal, schema-valid node skill with a shipped eval suite."""
    d = root / "skills" / name
    d.mkdir(parents=True)
    manifest = {
        "id": name,
        "kind": "node",
        "provides": ["pour_control"],
        "requires": ["object_pose"],
        "inputs": {"object_pose": {"schema": "pose7d_f32", "rate_hz": 30}},
        "outputs": {"joint_cmd": {"schema": "jointcmd9_f32", "latency_class": "soft_rt"}},
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
        (d / "eval_graph.yaml").write_text("nodes: []\n")
    return d


def _registry(root: Path) -> Path:
    """A registry root with the REAL schema and one core manifest."""
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
    """§8.4: validate → eval → evalcard → install. The installed manifest
    carries eval {suite, pass_rate, last_run} (CAP-1 shape) and lints
    against the real schema (CAP-3)."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    fake = _ok_rollout()
    result = register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    assert result["ok"] is True
    assert result["pass_rate"] == 0.75
    installed = yaml.safe_load((root / "registry" / "manifests" / "pour-arc.yaml").read_text())
    assert installed["eval"] == {
        "suite": "pour-arc_eval_v0",
        "pass_rate": 0.75,
        "last_run": "2026-07-25",
    }
    assert installed["origin"] == "agent-authored"
    # the eval ran with the skill's shipped config
    assert fake.calls and fake.calls[0]["tier"] == "T1" and fake.calls[0]["episodes"] == 4


def test_register_refuses_below_min_pass_rate(tmp_path):
    """§8.4 governance: a skill whose eval underperforms its own shipped
    threshold is NOT installed — no evalcard, no registry entry."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, eval_extra={"min_pass_rate": 0.9})
    with pytest.raises(RegistrationError, match="pass_rate"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")
    assert not (root / "registry" / "manifests" / "pour-arc.yaml").exists()


def test_register_refuses_core_id_collision(tmp_path):
    """Governance: a skill may not shadow a curated core manifest id."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, name="oracle-pose")
    with pytest.raises(RegistrationError, match="exists"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")


def test_register_refuses_non_agent_authored(tmp_path):
    """§9.4: the registration path is FOR agent-authored skills — hub
    manifests are curated by hand, not registered."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, manifest_extra={"origin": "hub"})
    with pytest.raises(RegistrationError, match="origin"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")


def test_register_refuses_schema_invalid_manifest(tmp_path):
    """CAP-3: a manifest that fails the JSON-Schema lint never reaches the
    eval stage (the rollout must not run)."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, manifest_extra={"safety_class": "chaotic"})
    fake = _ok_rollout()
    with pytest.raises(RegistrationError, match="schema|lint|invalid"):
        register_skill(skill_dir, root, run_rollout=fake, now="2026-07-25")
    assert not fake.calls


def test_register_refuses_missing_eval_suite(tmp_path):
    """§3 rule 3: a skill SHIPS its eval suite — no eval.yaml, no
    registration (CAP-6: an unevaluated motion skill is unusable)."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, omit_eval=True)
    with pytest.raises(RegistrationError, match="eval"):
        register_skill(skill_dir, root, run_rollout=_ok_rollout(), now="2026-07-25")


def test_register_refuses_failed_eval_run(tmp_path):
    """A refused/failed rollout is not a pass rate of zero — it is a
    failed registration with the rollout's refusal surfaced."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    bad = _ok_rollout(report={"ok": False, "refused": {"gate": "validate"}})
    with pytest.raises(RegistrationError, match="eval run failed"):
        register_skill(skill_dir, root, run_rollout=bad, now="2026-07-25")


def test_load_skill_reads_manifest_and_eval(tmp_path):
    root = _registry(tmp_path)
    skill_dir = _write_skill(root)
    skill = load_skill(skill_dir)
    assert skill.manifest["id"] == "pour-arc"
    assert skill.eval_cfg["suite"] == "pour-arc_eval_v0"
    assert skill.eval_cfg["min_pass_rate"] == 0.5


def test_cli_surface_parses():
    """CON-8: the CLI subcommand exists with the documented flags."""
    from aisle.harness.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["skill", "register", "skills/pour-arc"])
    assert args.command == "skill" and args.skill_command == "register"
    assert str(args.skill_dir) == "skills/pour-arc"


def test_cli_register_json_contract(tmp_path):
    """CON-8 end to end: JSON on stdout, exit 1 on a refused registration
    (missing eval suite here — no rollout machinery touched)."""
    root = _registry(tmp_path)
    skill_dir = _write_skill(root, omit_eval=True)
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
    assert out["ok"] is False and "eval" in out["error"]
