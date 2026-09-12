"""Trusted run entry point for already prepared matched engineering inputs.

The caller owns session admission, budgets, process supervision and construction
of the private configuration. This entry never creates study attestations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from aisle.harness.matched_runtime import verify_runtime, worker_interpreter
from aisle.harness.matched_surface import development_surface, record_surface
from aisle.harness.treatment_confinement import MacOSPolicy, wrap_verified_command
from aisle.harness.typed_snapshot import _read

ROOT = Path(__file__).resolve().parents[3]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate run configuration field")
        result[key] = value
    return result


def _load(path, digest, *, controller_root=None):
    path = Path(path).absolute()
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("run configuration requires an exact SHA-256")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("run configuration exceeds size limit")
    raw = _read(path.parent, path.name)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("run configuration hash differs")
    config = json.loads(raw, object_pairs_hook=_object)
    json.dumps(config, allow_nan=False)
    if type(config) is not dict or set(config) != {
        "schema_version",
        "purpose",
        "arm",
        "controller_root",
        "participant_root",
        "session_id",
        "plan_id",
        "run_id",
        "development",
        "runtime_record",
        "worker_adapter_sha256",
        "launch",
    }:
        raise ValueError("invalid run configuration fields")
    if (
        config["schema_version"] != "aisle.matched-run-config.v1"
        or config["purpose"] != "expert_parity"
    ):
        raise ValueError("run configuration is not unscored engineering")
    if config["arm"] not in {"typed", "monolithic"}:
        raise ValueError("unknown matched arm")
    for name in ("session_id", "run_id"):
        if (
            not isinstance(config[name], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", config[name]) is None
        ):
            raise ValueError("invalid run or session identity")
    if (
        not isinstance(config["plan_id"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", config["plan_id"]) is None
    ):
        raise ValueError("invalid plan identity")
    root, view = (Path(config[name]) for name in ("controller_root", "participant_root"))
    if (
        root != (ROOT if controller_root is None else Path(controller_root))
        or view.resolve() != view
        or not view.is_dir()
    ):
        raise ValueError("run roots differ from executing controller or canonical participant")
    if view.is_relative_to(root) or root.is_relative_to(view) or path.is_relative_to(view):
        raise ValueError("run configuration or controller overlaps participant authority")
    from aisle.harness.matched_session import _verify_development

    _verify_development(config["development"])
    if (
        not isinstance(config["worker_adapter_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", config["worker_adapter_sha256"]) is None
    ):
        raise ValueError("worker adapter identity is unresolved")
    verify_runtime(config["runtime_record"])
    return config


def _worker_binding(launch, config, config_path):
    if launch["attestation"]["adapter"]["sha256"] != config["worker_adapter_sha256"]:
        raise ValueError("worker adapter differs from the run identity")
    if launch["runtime_record"] != config["runtime_record"]:
        raise ValueError("worker runtime differs from bound run runtime")
    if launch["python_sha256"] != hashlib.sha256(worker_interpreter().read_bytes()).hexdigest():
        raise ValueError("worker and controller interpreter identities differ")
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    readable = (*policy.visible_roots, *policy.runtime_read_roots, *policy.output_roots)
    if any(config_path.is_relative_to(p) for p in readable) or not any(
        config_path.is_relative_to(p) for p in policy.hidden_roots
    ):
        raise ValueError("run configuration is not private from worker authority")
    for root in (config["controller_root"], config["participant_root"]):
        if not any(Path(root).is_relative_to(p) for p in policy.hidden_roots):
            raise ValueError("worker lacks hidden controller or participant binding")
    return policy


def _typed(config, path):
    from aisle.harness.typed_graph_stage import select_rollout_stage
    from aisle.harness.typed_node_host import load_host_config

    launch = config["launch"]
    if type(launch) is dict and set(launch) == {"provider"}:
        from aisle.harness.matched_dynamic_run import configured_typed_provider

        return configured_typed_provider(config, path)
    if set(launch) != {"stages"} or not isinstance(launch["stages"], list) or not launch["stages"]:
        raise ValueError("typed run requires prepared launch stages")
    stages = []
    for selected in launch["stages"]:
        if set(selected) != {"root", "stage_id"}:
            raise ValueError("invalid typed stage selection")
        stage = Path(selected["root"])
        receipt = json.loads(_read(stage, "stage.json"))
        if receipt["immutable_id"] != selected["stage_id"]:
            raise ValueError("typed stage identity differs")
        snapshot = receipt["snapshot_record"]
        if record_surface(snapshot) != development_surface(config["development"]):
            raise ValueError("typed stage task surface differs from run")
        if snapshot["participant_root"] != config["participant_root"]:
            raise ValueError("typed stage belongs to another participant")
        for name, digest in snapshot["inputs"]["participant"].items():
            if hashlib.sha256(_read(Path(config["participant_root"]), name)).hexdigest() != digest:
                raise ValueError("participant changed after typed snapshot construction")
        for binding in receipt["hosts"].values():
            host = load_host_config(binding["config_path"], binding["config_sha256"])
            _worker_binding(host["launch"], config, path)
        stages.append((stage, receipt))
    graph = (
        Path(stages[0][1]["snapshot_record"]["snapshot_root"])
        / development_surface(config["development"]).typed_graph
    )
    history = []
    for index in range(len(stages)):
        select_rollout_stage(
            lambda i: stages[i],
            index,
            history,
            authored_bytes=graph.read_bytes(),
            graph=graph,
            controller_root=ROOT,
            embodiment=config["development"]["embodiment"],
        )
    return graph, lambda index: stages[index]


def _monolithic(config, path):
    from aisle.monolith.worker_config import _load as load_worker
    from aisle.monolith.worker_launch import verify_worker_launch

    binding = config["launch"]
    if type(binding) is dict and set(binding) == {"provider"}:
        from aisle.harness.matched_dynamic_run import configured_monolithic_provider

        binding = configured_monolithic_provider(config, path)
    if set(binding) != {"worker_config", "worker_config_sha256"}:
        raise ValueError("monolithic run requires its worker configuration")
    worker = load_worker(binding["worker_config"], binding["worker_config_sha256"])
    module = (
        Path(config["participant_root"])
        / development_surface(config["development"]).monolithic_module
    )
    if hashlib.sha256(_read(module.parent, module.name)).hexdigest() != worker["module_sha256"]:
        raise ValueError("monolithic source differs from worker binding")
    if worker["embodiment"] != config["development"]["embodiment"]:
        raise ValueError("worker embodiment differs from run")
    launch = worker["launch"]
    policy = _worker_binding(launch, config, path)
    compiled = verify_worker_launch(
        **{
            key: policy if key == "policy" else launch[key]
            for key in (
                "bundle",
                "bundle_manifest",
                "runtime_record",
                "source_roots",
                "policy",
                "python",
                "python_sha256",
                "environment",
                "environment_record",
            )
        }
    )
    wrap_verified_command(
        [launch["python"], "-I", "-B", "-c", "pass"],
        compiled,
        launch["profile_path"],
        launch["attestation"],
    )
    if Path(worker["output_root"]).exists():
        raise ValueError("monolithic worker output already exists; resume refused")
    return module, binding


def run_configured(path, digest):
    """Verify a private configuration and dispatch to the actual selected arm API."""
    try:
        path = Path(path).absolute()
        config = _load(path, digest)
        development = config["development"]
        common = dict(
            root=ROOT,
            seeds=development["seeds"],
            episodes=len(development["seeds"]),
            tier=development["tier"],
            embodiment=development["embodiment"],
            run_id=config["run_id"],
            timeout_s=development["timeout_s"],
            no_idea_gate=False,
            record_simulator_work=True,
        )
        runs = ROOT / "runs"
        if runs.resolve() != runs or (runs.exists() and not runs.is_dir()):
            raise ValueError("run output root is redirected or invalid")
        destination = runs / config["run_id"]
        if destination.exists() or destination.is_symlink():
            raise ValueError("run output already exists; resume refused")
        if config["arm"] == "typed":
            from aisle.harness.cli import _branch
            from aisle.harness.rollout import rollout

            graph, factory = _typed(config, path)
            result = rollout(
                **common,
                graph=graph,
                typed_stage_factory=factory,
                reset_mode=development["reset"],
                verifier=development["verifier"],
                branch=_branch(ROOT),
            )
        else:
            from aisle.harness.monolith import run
            from aisle.harness.monolithic_run_evidence import retain_worker_attempt

            module, binding = _monolithic(config, path)
            with retain_worker_attempt(
                binding["worker_config"],
                binding["worker_config_sha256"],
                module,
                path.parent / "run-controller/monolithic-worker",
            ) as worker_evidence:
                try:
                    result = run(
                        **common,
                        module=module,
                        **binding,
                        task_surface=development_surface(development).identity,
                    )
                except Exception as exc:
                    result = {"ok": False, "infrastructure_invalid": True, "error": str(exc)}
            result["worker_evidence"] = worker_evidence
            if not worker_evidence["ok"]:
                result.update(ok=False, infrastructure_invalid=True)
                result["error"] = "; ".join(
                    filter(None, [result.get("error"), *worker_evidence["errors"]])
                )
        _load(path, digest)
        return {
            **result,
            "matched_run": {
                "session_id": config["session_id"],
                "plan_id": config["plan_id"],
                "arm": config["arm"],
                "config_sha256": digest,
            },
        }
    except Exception as exc:
        return {"ok": False, "infrastructure_invalid": True, "error": str(exc)}


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)

    parser = Parser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        result = run_configured(args.config, args.config_sha256)
    except ValueError as exc:
        result = {"ok": False, "infrastructure_invalid": True, "error": str(exc)}
    print(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
