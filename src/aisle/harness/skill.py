"""Skill registration (T18; design doc §8.4 item 1, §3 rule 3, §9.2;
CAP-5 as amended by the curated-core spec change).

A skill is a directory shipping everything the library needs (§9.2):
`skill.yaml` (a CAP-1 manifest, origin agent-authored, eval null until
registered), its node code / subgraph YAML, and `eval.yaml` — the
mini-rollout config that IS the skill's eval suite, whose graph MUST use
the candidate (a node with the skill's id).

Registration is a STAGED transaction (PR #30 review): the candidate
manifest — carrying a clearly-labelled provisional evalcard so CAP-6's
motion gate can pass during its own evaluation — is installed into the
registry FIRST, the whole registry is linted (CAP-2/3), the shipped eval
rollout runs against the STAGED candidate (an update evaluates the NEW
manifest, never the old one; a fresh candidate is discoverable instead
of MANIFEST_MISSING), and only a measured pass rate at or above the
skill's own `min_pass_rate` finalizes the evalcard. ANY failure rolls
the registry back to its prior state exactly.

Governance (§9.4 + CAP-5): curated-core ids are refused from the
single-sourced `registry/schema/curated_core.toml` REGARDLESS of current
file state (deleting a core manifest does not open its id); origin must
be agent-authored; the PR a human merges is the trust boundary.

Pure logic; the rollout runner is injected (CON-12/CON-5: unit tests
never touch sim, `last_run` comes from an injected clock). Every refusal
is a RegistrationError the CLI surfaces as {ok: false, error} (CON-8) —
malformed YAML, bad seed specs, and unparseable configs included.
"""

from __future__ import annotations

import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from aisle.harness.registry import lint, load_capability_schema, manifest_schema_errors
from aisle.harness.rollout import parse_seed_range

EVAL_REQUIRED = ("suite", "graph", "tier", "episodes", "seeds", "embodiment", "min_pass_rate")
CURATED_CORE = "registry/schema/curated_core.toml"


class RegistrationError(RuntimeError):
    """A refused registration — the reason is the message (CON-8: the CLI
    surfaces it as {ok: false, error})."""


@dataclass(frozen=True)
class Skill:
    path: Path
    manifest: dict
    eval_cfg: dict


def _load_yaml(path: Path, what: str) -> dict:
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as bad:
        raise RegistrationError(f"{what} is not valid YAML: {bad}") from bad
    if not isinstance(doc, dict):
        raise RegistrationError(f"{what} is not a mapping")
    return doc


def load_skill(skill_dir: Path) -> Skill:
    """Read the skill directory's manifest and shipped eval suite."""
    manifest_path = skill_dir / "skill.yaml"
    if not manifest_path.exists():
        raise RegistrationError(f"no skill.yaml in {skill_dir}")
    manifest = _load_yaml(manifest_path, "skill.yaml")
    eval_path = skill_dir / "eval.yaml"
    if not eval_path.exists():
        raise RegistrationError(
            "no eval.yaml: a skill SHIPS its eval suite (design doc §3 rule 3) — "
            "an unevaluated skill cannot be registered (CAP-6)"
        )
    eval_cfg = _load_yaml(eval_path, "eval.yaml")
    missing = [k for k in EVAL_REQUIRED if k not in eval_cfg]
    if missing:
        raise RegistrationError(f"eval.yaml missing keys: {missing}")
    # CON-8: malformed eval fields refuse cleanly, never traceback
    try:
        parse_seed_range(str(eval_cfg["seeds"]))
        int(eval_cfg["episodes"])
        float(eval_cfg["min_pass_rate"])
    except (ValueError, TypeError) as bad:
        raise RegistrationError(f"eval.yaml field invalid: {bad}") from bad
    return Skill(path=skill_dir, manifest=manifest, eval_cfg=eval_cfg)


def curated_ids(root: Path) -> frozenset[str]:
    """The CAP-5 curated core, from its single Class-C source — never from
    mutable manifest-file presence (PR #30: deleting a core file must not
    open its id)."""
    path = root / CURATED_CORE
    if not path.exists():
        raise RegistrationError(f"{CURATED_CORE} missing — cannot establish the curated core")
    try:
        return frozenset(tomllib.loads(path.read_text())["core"])
    except (tomllib.TOMLDecodeError, KeyError) as bad:
        raise RegistrationError(f"{CURATED_CORE} unreadable: {bad}") from bad


def _eval_graph_path(skill: Skill, root: Path) -> Path:
    graph = root / str(skill.eval_cfg["graph"])
    if not graph.exists():
        raise RegistrationError(f"eval graph {skill.eval_cfg['graph']!r} not found")
    return graph


def validate_skill(skill: Skill, root: Path) -> None:
    """Schema + governance + candidate-binding checks, BEFORE any staging
    or eval spend."""
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
    if skill_id in curated_ids(root):
        raise RegistrationError(
            f"id {skill_id!r} is on the CURATED core list ({CURATED_CORE}) — "
            "a skill may never shadow a core capability (CAP-5)"
        )
    source = manifest.get("source", "")
    if not (root / source).exists() and not (skill.path / Path(source).name).exists():
        raise RegistrationError(f"skill source {source!r} not found")
    graph = _eval_graph_path(skill, root)
    # the eval suite must be BOUND to the candidate (PR #30): an unrelated
    # green graph must not be able to mint this skill's evalcard
    doc = _load_yaml(graph, "eval graph")
    node_ids = {n.get("id") for n in doc.get("nodes", []) if isinstance(n, dict)}
    if skill_id not in node_ids:
        raise RegistrationError(
            f"eval graph does not use the candidate: no node with id {skill_id!r} "
            f"(nodes: {sorted(i for i in node_ids if i)})"
        )


def _provisional_manifest(skill: Skill, now: str) -> dict:
    """The staged candidate: CAP-6 requires a non-null eval for motion
    skills DURING their own evaluation — the provisional card is clearly
    labelled and never survives a failed registration."""
    return {
        **skill.manifest,
        "eval": {
            "suite": f"{skill.eval_cfg['suite']} (provisional)",
            "pass_rate": 0.0,
            "last_run": f"provisional-{now}",
        },
    }


def _lint_or(root: Path, message: str) -> None:
    report = lint(root)
    if not report.get("ok"):
        raise RegistrationError(f"{message}: {report.get('errors', [])[:3]}")


def run_skill_eval(skill: Skill, root: Path, run_rollout, run_id: str) -> float:
    """The shipped mini-rollout against the STAGED candidate: returns the
    measured pass rate (pass1)."""
    cfg = skill.eval_cfg
    report = run_rollout(
        root=root,
        graph=_eval_graph_path(skill, root),
        tier=str(cfg["tier"]),
        episodes=int(cfg["episodes"]),
        seeds=parse_seed_range(str(cfg["seeds"])),
        reset_mode=str(cfg.get("reset", "teleport")),
        verifier=str(cfg.get("verifier", "oracle")),
        run_id=run_id,
        branch="skill-eval",
        no_idea_gate=True,  # registration machinery, logged (ADR-22)
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
    """validate → STAGE → lint → eval → evalcard → final lint (§8.4);
    every failure rolls the registry back exactly."""
    root = Path(root)
    skill = load_skill(Path(skill_dir))
    validate_skill(skill, root)

    installed = root / "registry" / "manifests" / f"{skill.manifest['id']}.yaml"
    prior = installed.read_text() if installed.exists() else None
    if prior is not None:
        existing = yaml.safe_load(prior)
        if not isinstance(existing, dict) or existing.get("origin") != "agent-authored":
            raise RegistrationError(
                f"manifest id {skill.manifest['id']!r} already exists and is not a "
                "registered skill — refusing to replace it"
            )

    def rollback() -> None:
        if prior is None:
            installed.unlink(missing_ok=True)
        else:
            installed.write_text(prior)

    installed.write_text(yaml.safe_dump(_provisional_manifest(skill, now), sort_keys=False))
    try:
        _lint_or(root, "staged registry fails lint (CAP-2/3)")
        eval_run_id = run_id or f"skill-{skill.manifest['id']}-{now}-{uuid.uuid4().hex[:6]}"
        pass_rate = run_skill_eval(skill, root, run_rollout, eval_run_id)
        minimum = float(skill.eval_cfg["min_pass_rate"])
        if pass_rate < minimum:
            raise RegistrationError(
                f"measured pass_rate {pass_rate:.3f} below the skill's shipped "
                f"min_pass_rate {minimum:.3f} — not installed"
            )
        final = dict(skill.manifest)
        final["eval"] = {
            "suite": str(skill.eval_cfg["suite"]),
            "pass_rate": round(pass_rate, 4),
            "last_run": now,
        }
        installed.write_text(yaml.safe_dump(final, sort_keys=False))
        _lint_or(root, "final registry fails lint (CAP-2/3)")
    except BaseException:
        rollback()
        raise
    return {
        "ok": True,
        "id": final["id"],
        "pass_rate": pass_rate,
        "evalcard": final["eval"],
        "installed": str(installed),
        "eval_run_id": eval_run_id,
        "governance": "open a PR with this manifest; a human merges it (§9.4)",
    }
