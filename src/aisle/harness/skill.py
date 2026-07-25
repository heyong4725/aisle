"""Skill registration (T18; design doc §8.4 item 1, §3 rule 3, §9.2).

A skill is a directory shipping everything the library needs (§9.2):
`skill.yaml` (a CAP-1 manifest, origin agent-authored, eval null until
registered), its node code / subgraph YAML, and `eval.yaml` — the
mini-rollout config that IS the skill's eval suite. `register_skill`
validates the manifest against the capability schema (CAP-3), runs the
shipped eval, refuses below the skill's own `min_pass_rate`, writes the
evalcard (CAP-1/CAP-6 `eval {suite, pass_rate, last_run}`), and installs
the manifest into `registry/manifests/` — where the validator and
`registry search` pick it up like any capability. Governance (§9.4): the
CLI writes files; a HUMAN merges the PR that carries them, and core
(non-agent-authored) manifest ids can never be shadowed.

Pure logic; the rollout runner is injected (CON-12/CON-5: unit tests
never touch sim, `last_run` comes from an injected clock).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from aisle.harness.registry import load_capability_schema, manifest_schema_errors
from aisle.harness.rollout import parse_seed_range

EVAL_REQUIRED = ("suite", "graph", "tier", "episodes", "seeds", "embodiment", "min_pass_rate")


class RegistrationError(RuntimeError):
    """A refused registration — the reason is the message (CON-8: the CLI
    surfaces it as {ok: false, error})."""


@dataclass(frozen=True)
class Skill:
    path: Path
    manifest: dict
    eval_cfg: dict


def load_skill(skill_dir: Path) -> Skill:
    """Read the skill directory's manifest and shipped eval suite."""
    manifest_path = skill_dir / "skill.yaml"
    if not manifest_path.exists():
        raise RegistrationError(f"no skill.yaml in {skill_dir}")
    manifest = yaml.safe_load(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise RegistrationError("skill.yaml is not a mapping")
    eval_path = skill_dir / "eval.yaml"
    if not eval_path.exists():
        raise RegistrationError(
            "no eval.yaml: a skill SHIPS its eval suite (design doc §3 rule 3) — "
            "an unevaluated skill cannot be registered (CAP-6)"
        )
    eval_cfg = yaml.safe_load(eval_path.read_text())
    if not isinstance(eval_cfg, dict):
        raise RegistrationError("eval.yaml is not a mapping")
    missing = [k for k in EVAL_REQUIRED if k not in eval_cfg]
    if missing:
        raise RegistrationError(f"eval.yaml missing keys: {missing}")
    return Skill(path=skill_dir, manifest=manifest, eval_cfg=eval_cfg)


def validate_skill(skill: Skill, root: Path) -> None:
    """Schema + governance checks, BEFORE any eval spend."""
    manifest = skill.manifest
    if manifest.get("origin") != "agent-authored":
        raise RegistrationError(
            f"origin must be 'agent-authored' for registration, got "
            f"{manifest.get('origin')!r} — hub manifests are curated by hand (§9.4)"
        )
    schema = load_capability_schema(root)
    errors = manifest_schema_errors(schema, manifest)
    if errors:
        raise RegistrationError(f"manifest fails the capability schema (CAP-3): {errors[:3]}")
    skill_id = manifest["id"]
    installed = root / "registry" / "manifests" / f"{skill_id}.yaml"
    if installed.exists():
        existing = yaml.safe_load(installed.read_text())
        if existing.get("origin") != "agent-authored":
            raise RegistrationError(
                f"manifest id {skill_id!r} already exists in the curated registry — "
                "a skill may not shadow a core capability"
            )
    source = skill.manifest.get("source", "")
    if not (root / source).exists() and not (skill.path / Path(source).name).exists():
        raise RegistrationError(f"skill source {source!r} not found")
    eval_graph = root / skill.eval_cfg["graph"]
    if not eval_graph.exists() and not (skill.path / Path(skill.eval_cfg["graph"]).name).exists():
        raise RegistrationError(f"eval graph {skill.eval_cfg['graph']!r} not found")


def run_skill_eval(skill: Skill, root: Path, run_rollout, run_id: str) -> float:
    """The shipped mini-rollout: returns the measured pass rate (pass1)."""
    cfg = skill.eval_cfg
    report = run_rollout(
        root=root,
        graph=root / cfg["graph"],
        tier=str(cfg["tier"]),
        episodes=int(cfg["episodes"]),
        seeds=parse_seed_range(str(cfg["seeds"])),
        reset_mode=str(cfg.get("reset", "teleport")),
        verifier=str(cfg.get("verifier", "oracle")),
        run_id=run_id,
        branch="skill-eval",
        no_idea_gate=True,  # the eval suite is registration machinery, logged
        embodiment=str(cfg["embodiment"]),
        env_baseline=str(cfg.get("env_baseline", "local")),
    )
    if not report.get("ok"):
        detail = report.get("refused") or report.get("error") or "no episodes"
        raise RegistrationError(f"eval run failed before scoring: {detail}")
    return float(report.get("pass1", 0.0))


def register_skill(
    skill_dir: Path, root: Path, run_rollout, now: str, run_id: str | None = None
) -> dict:
    """validate → eval → evalcard → install (§8.4). Returns the CON-8
    report; raises RegistrationError on any refusal."""
    skill = load_skill(Path(skill_dir))
    validate_skill(skill, root)
    pass_rate = run_skill_eval(
        skill, root, run_rollout, run_id or f"skill-{skill.manifest['id']}-{now}"
    )
    minimum = float(skill.eval_cfg["min_pass_rate"])
    if pass_rate < minimum:
        raise RegistrationError(
            f"measured pass_rate {pass_rate:.3f} below the skill's shipped "
            f"min_pass_rate {minimum:.3f} — not installed"
        )
    manifest = dict(skill.manifest)
    manifest["eval"] = {
        "suite": str(skill.eval_cfg["suite"]),
        "pass_rate": round(pass_rate, 4),
        "last_run": now,
    }
    installed = root / "registry" / "manifests" / f"{manifest['id']}.yaml"
    installed.write_text(yaml.safe_dump(manifest, sort_keys=False))
    return {
        "ok": True,
        "id": manifest["id"],
        "pass_rate": pass_rate,
        "evalcard": manifest["eval"],
        "installed": str(installed),
        "governance": "open a PR with this manifest; a human merges it (§9.4)",
    }
