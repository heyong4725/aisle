"""Private controller checkout for actual, unscored matched-run acceptance.

This supplies source and Git provenance only. Runtime, worker, frontend and
confinement bindings still belong to the admitted session fixture.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path, PurePosixPath


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def activate_prepared_controller(controller):
    """Select one controller checkout before imports; never evict mixed loaded code."""
    import sys

    controller = Path(controller).resolve(strict=True)
    for name, module in tuple(sys.modules.items()):
        if (
            name == "aisle"
            or name.startswith("aisle.")
            or name in {"campaign", "matched_campaign", "env_hash"}
        ):
            path = getattr(module, "__file__", None)
            if path is None or not Path(path).resolve().is_relative_to(controller):
                raise ValueError(
                    "prepared controller import already belongs to another checkout: " + name
                )
    sys.path[:0] = [str(controller / "tools"), str(controller / "src")]


def prepare_run_checkout(source, destination, *, controller_files):
    """Copy committed provenance and explicit current sources into a fresh checkout.

    The origin URL remains the real remote; rollout's trusted-baseline fetch is
    neither replaced with a local ref nor skipped. Ignored workspace state is
    excluded, while newly implemented controller files are included explicitly.
    """
    source, destination = Path(source).resolve(strict=True), Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve() != destination:
        raise ValueError("run controller requires a fresh canonical destination")
    head = _git(source, "rev-parse", "HEAD").decode().strip()
    origin = _git(source, "remote", "get-url", "origin").decode().strip()
    names = {name.decode() for name in _git(source, "ls-files", "-z").split(b"\0") if name}
    names.update(controller_files)
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts or str(path) != name:
            raise ValueError("noncanonical run controller source")
        if (source / name).is_symlink():
            raise ValueError("redirected run controller source")
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            str(source),
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    _git(destination, "checkout", "--quiet", "--detach", head)
    _git(destination, "remote", "set-url", "origin", origin)
    hashes = {}
    for name in sorted(names):
        original, target = source / name, destination / name
        if not original.exists():
            if name in controller_files:
                raise ValueError("required run controller source is missing")
            target.unlink(missing_ok=True)
            continue
        if not original.is_file() or original.resolve() != original:
            raise ValueError("run controller source is not a canonical file")
        raw = original.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        shutil.copymode(original, target)
        if original.read_bytes() != raw:
            raise ValueError("run controller source changed during acquisition")
        hashes[name] = hashlib.sha256(raw).hexdigest()
    if _git(source, "rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("source baseline changed during acquisition")
    return {
        "schema_version": "aisle.conformance-run-checkout.v1",
        "purpose": "expert_parity",
        "base_oid": head,
        "origin": origin,
        "source_files": hashes,
        "study_collection_authorized": False,
    }


def prepare_run_idea(controller, *, timestamp):
    """HAR-8: log the engineering hypothesis using rollout's branch convention."""
    from aisle.harness.cli import _branch, _git_sha
    from aisle.harness.ideas import log_idea

    controller = Path(controller)
    branch = _branch(controller)
    entry = log_idea(
        controller,
        branch,
        "The admitted frontend can execute the prepared expert through harness.run.",
        timestamp,
        _git_sha(controller),
        expect="One engineering episode with retained controller evidence; no study claim.",
    )
    return {"branch": branch, "entry": entry}


def prepare_worker_declarations(
    base,
    *,
    controller,
    views,
    evidence_root,
    snapshot_storage,
    runtime,
    python,
    adapter,
    development,
):
    """Reserve both arms' worker inputs with an explicitly synthetic adapter."""
    from test_treatment_confinement import _attestation

    from aisle.harness.treatment_ambient import build_declared_environment
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    worker_timeout = run_budget_limits(development)["timeout_s"]
    base = Path(base)
    base.mkdir()
    hidden = (Path(controller), *(Path(view) for view in views.values()), Path(evidence_root))
    runtime_roots = tuple(Path(root) for root in runtime["trees"])
    worker_python = Path(python).absolute()
    if not any(worker_python.is_relative_to(root) for root in runtime_roots):
        raise ValueError("worker interpreter invocation must be inside the bound runtime")
    python = Path(python).resolve(strict=True)
    python_sha256 = hashlib.sha256(python.read_bytes()).hexdigest()
    declarations = {}
    for arm in ("typed", "monolithic"):
        bundle = base / (arm + "-bundle")
        bundle.mkdir()
        workers = (
            ("segmented-pose", "grasp-planner-topdown", "ik-trajectory", "task-state-machine")
            if arm == "typed"
            else ("monolithic",)
        )
        rows = {}
        for worker in workers:
            environment, record = build_declared_environment(
                base / (worker + "-home"), source_env={}
            )
            policy = MacOSPolicy(
                visible_roots=(bundle,),
                output_roots=(Path(record["home"]),),
                runtime_read_roots=runtime_roots,
                allowed_executables=(python,),
                hidden_roots=tuple(dict.fromkeys((*hidden, Path(snapshot_storage))))
                if arm == "typed"
                else hidden,
                network_policy="deny-external",
            )
            compiled = compile_macos_profile(policy)
            profile = base / (worker + ".sb")
            profile.write_text(compiled.text)
            row = {
                "bundle": str(bundle),
                "policy": policy.canonical_dict(),
                "profile_path": str(profile),
                "attestation": _attestation(compiled, profile, Path(adapter)),
                "python": str(worker_python),
                "python_sha256": python_sha256,
                "environment": environment,
                "environment_record": record,
                "runtime_record": runtime,
                "timeout_s": worker_timeout,
            }
            if arm == "monolithic":
                row.update(max_primitive_calls=100000, max_handles=1024)
            rows[worker] = row
        declarations[arm] = [rows if arm == "typed" else rows["monolithic"]]
    return declarations


def prepare_controller_bindings(
    base, *, controller, views, evidence_root, runtime, python, adapter, path
):
    """Declare the validator and private run controllers without widening worker grants."""
    import sys

    from test_treatment_confinement import _attestation

    from aisle.harness.treatment_ambient import (
        amend_declared_environment,
        build_declared_environment,
    )
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile
    from aisle.harness.typed_validation import build_validation_bundle

    if sys.platform == "darwin" and shutil.which("sysctl", path=path) is None:
        raise ValueError("macOS simulator PATH must resolve sysctl for MuJoCo initialization")
    controller_python = Path(python).absolute()
    if not any(controller_python.is_relative_to(Path(root)) for root in runtime["trees"]):
        raise ValueError("controller interpreter invocation must be inside the bound runtime")
    base = Path(base)
    base.mkdir()
    python = Path(python).resolve(strict=True)
    digest = hashlib.sha256(python.read_bytes()).hexdigest()
    bundle = base / "validator"
    manifest = build_validation_bundle(bundle)
    storage = base / "validation-snapshots"
    storage.mkdir()
    environment, record = build_declared_environment(base / "validator-home", source_env={})
    policy = MacOSPolicy(
        visible_roots=(bundle, storage),
        output_roots=(Path(record["home"]),),
        runtime_read_roots=tuple(Path(root) for root in runtime["trees"]),
        allowed_executables=(python,),
        hidden_roots=(
            Path(controller),
            *(Path(view) for view in views.values()),
            Path(evidence_root),
        ),
        network_policy="deny-external",
    )
    compiled = compile_macos_profile(policy)
    profile = base / "validator.sb"
    profile.write_text(compiled.text)
    validation = {
        "schema_version": "aisle.typed-validation-binding.v1",
        "bundle": str(bundle),
        "bundle_manifest": manifest,
        "snapshot_storage": str(storage),
        "policy": policy.canonical_dict(),
        "profile_path": str(profile),
        "attestation": _attestation(compiled, profile, Path(adapter)),
        "python": str(controller_python),
        "python_sha256": digest,
        "environment": environment,
        "environment_record": record,
    }
    controllers = {}
    for arm in ("typed", "monolithic"):
        environment, record = build_declared_environment(
            base / (arm + "-controller-home"), source_env={"PATH": path}
        )
        environment["PYTHONPATH"] = ":".join(
            [str(Path(controller) / "src"), *sorted(runtime["trees"])]
        )
        amend_declared_environment(
            environment,
            record,
            added_keys=["PYTHONPATH"],
            reason=(
                "Bind trusted run-controller children to the declared source and runtime imports"
            ),
        )
        controllers[arm] = {
            "schema_version": "aisle.matched-run-controller.v1",
            "python": str(controller_python),
            "python_sha256": digest,
            "environment": environment,
            "environment_record": record,
        }
    return {"typed_validation": validation, "run_controller": controllers}


def run_budget_limits(development):
    """Allow the standard rollout plus explicit controller and frontend overhead."""
    from aisle.harness.rollout import GENESIS_BUILD_BUDGET_S, resolve_budgets

    timeout = (
        GENESIS_BUILD_BUDGET_S
        + len(development["seeds"])
        * resolve_budgets(development["tier"], development["verifier"])[1]
    )
    # Size the enclosing engineering transaction for validation, worker/source
    # preparation, and postflight/runtime/RPC retention (600 seconds each).
    # These are sizing allowances, not separate stage deadlines: the full tool
    # transaction remains subject to one wall ceiling. Run0014 already spent
    # 690 seconds before controller launch, exceeding the old combined 600.
    # Both arms receive the same allowance; rollout and worker limits stay fixed.
    controller_overhead = 3 * 600
    frontend_overhead = 600
    return {
        "timeout_s": timeout,
        "tool_wall_ceiling_s": timeout + controller_overhead,
        "wall_ceiling_s": timeout + controller_overhead + frontend_overhead,
    }


def load_run_setup(base):
    """Load the retained preparation inputs for a fresh actual frontend session."""
    import json

    from aisle.harness.monolith import interface_report, table_report
    from aisle.harness.treatment_ambient import amend_declared_environment

    base = Path(base)
    surface = [
        table_report(base / "controller", write=False),
        interface_report(base / "controller"),
    ]
    if any(not report["ok"] for report in surface):
        raise ValueError("prepared controller surface is stale: " + json.dumps(surface))

    def read(name):
        return json.loads((base / name).read_bytes())

    inputs = read("admission-inputs.json")
    bindings = read("controller-bindings.json")
    runtime = read("simulation-runtime.json")
    for binding in bindings["run_controller"].values():
        environment, record = binding["environment"], binding["environment_record"]
        if "PYTHONPATH" not in environment:
            environment["PYTHONPATH"] = ":".join(
                [str(base / "controller/src"), *sorted(runtime["trees"])]
            )
            amend_declared_environment(
                environment,
                record,
                added_keys=["PYTHONPATH"],
                reason=(
                    "Bind trusted run-controller children to the d"
                    "eclared source and runtime imports"
                ),
            )
    return {
        **inputs,
        **bindings,
        "tool_runtime": runtime,
        "root": base / "controller",
        "views": {arm: Path(view) for arm, view in read("pair-inputs.json")["views"].items()},
        "view_baseline": read("view-baseline.json")
        if (base / "view-baseline.json").exists()
        else None,
        "worker_preparations": read("worker-declarations.json"),
    }


def reference_run_admission(session):
    """Acquire a prior admission for configuration matching, not case qualification."""
    from urllib.parse import urlsplit

    from aisle.harness.frontend_conformance import _proof_document

    session = Path(session).absolute()
    if session.resolve() != session:
        raise ValueError("reference session is redirected")
    record = _proof_document(
        (session / "matched-session.json").read_bytes(), "aisle.matched-session-evidence.v1"
    )
    raw = (session / "admission.json").read_bytes()
    admission = _proof_document(raw, "aisle.matched-session-plan.v1")
    if (
        record["ok"] is not True
        or record["plan_id"] != admission["immutable_id"]
        or hashlib.sha256(raw).hexdigest() != record["artifacts"]["admission.json"]
    ):
        raise ValueError("reference admission differs from its successful receipt")
    urls = {row["provider"]["base_url"] for row in admission["launch_bindings"].values()}
    if len(urls) != 1:
        raise ValueError("paired provider addresses differ")
    url = urlsplit(urls.pop())
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.path != "/v1"
        or url.port is None
        or url.port == 0
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("reference provider is not the local fixture listener")
    return admission, ("127.0.0.1", url.port)


def require_same_run_configuration(plan, reference, *, fault=None):
    """Refuse a paired run before launch if controller or launch inputs changed."""
    from aisle.harness.frontend_conformance import (
        configuration_digest,
        execution_digest,
        fault_launch,
    )
    from aisle.harness.matched_session import CONTROLLER_FILES

    if execution_digest(plan) != execution_digest(reference):
        raise ValueError("paired run execution bindings differ")
    for arm in ("typed", "monolithic"):
        expected_launch = reference["launch_bindings"][arm]
        if fault is not None:
            expected_launch = fault_launch(expected_launch, fault)
        if configuration_digest(
            plan["arms"][arm], plan["launch_bindings"][arm]
        ) != configuration_digest(reference["arms"][arm], expected_launch):
            raise ValueError("paired run frontend configuration differs")
    if any(
        plan["surface"]["artifact_hashes"][name] != reference["surface"]["artifact_hashes"][name]
        for name in CONTROLLER_FILES
    ):
        raise ValueError("paired run controller sources differ")


def runtime_references(value):
    """Bind repeated runtime declarations by their already admitted content identity."""
    if isinstance(value, dict):
        if value.get("schema_version") == "aisle.matched-runtime.v1":
            return {"runtime_id": value["immutable_id"]}
        return {key: runtime_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [runtime_references(item) for item in value]
    return value


def worker_preparation_runs(declarations, arm):
    """Group launch declarations by controller run, retaining typed node maps."""
    return [declarations[arm]] if arm == "typed" else declarations[arm]


def _run_view_paths(base, setup):
    base = Path(base).resolve(strict=True)
    views = {arm: Path(path).absolute() for arm, path in setup["views"].items()}
    if set(views) != {"typed", "monolithic"}:
        raise ValueError("prepared views require both arms")
    protected = [Path(setup["root"]), base / "retained", *map(Path, setup["tool_runtime"]["trees"])]
    for view in views.values():
        if (
            view == base
            or not view.is_relative_to(base)
            or view.resolve() != view
            or not view.is_dir()
            or any(view.is_relative_to(path) or path.is_relative_to(view) for path in protected)
            or any(
                view != other and (view.is_relative_to(other) or other.is_relative_to(view))
                for other in views.values()
            )
        ):
            raise ValueError("prepared view overlaps protected or redirected paths")
    if len(set(views.values())) != 2:
        raise ValueError("prepared views overlap")
    return base, views


def _run_view_inventory(root):
    import stat

    inventory = {}
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("prepared baseline contains a redirected path")
        mode = path.stat().st_mode
        row = {"mode": stat.S_IMODE(mode)}
        if stat.S_ISDIR(mode):
            row["kind"] = "directory"
        elif stat.S_ISREG(mode):
            row.update(kind="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            raise ValueError("prepared baseline contains a special file")
        inventory[str(path.relative_to(root))] = row
    return inventory


def _fresh_view_archive(base, path):
    path = Path(path).absolute()
    if (
        path.exists()
        or path.is_symlink()
        or path.resolve() != path
        or path == base / "retained"
        or not path.is_relative_to(base / "retained")
    ):
        raise ValueError("prepared view archive must be fresh and retained")
    return path


def capture_run_views(base, setup, output):
    """Capture original private fixture views before any mutating matrix case."""
    import json

    base, views = _run_view_paths(base, setup)
    output = _fresh_view_archive(base, output)
    inventories = {arm: _run_view_inventory(view) for arm, view in views.items()}
    output.mkdir(parents=True)
    for arm, view in views.items():
        shutil.copytree(view, output / arm)
        if (
            _run_view_inventory(view) != inventories[arm]
            or _run_view_inventory(output / arm) != inventories[arm]
        ):
            raise ValueError("prepared view changed during baseline capture")
    record = {
        "schema_version": "aisle.conformance-view-baseline.v1",
        "root": str(output),
        "views": {arm: str(view) for arm, view in views.items()},
        "inventories": inventories,
    }
    record["immutable_id"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return record


def restore_run_views(base, setup, baseline, archive):
    """Restore an idle prepared pair only after validating and staging both baselines."""
    import json

    base, views = _run_view_paths(base, setup)
    archive = _fresh_view_archive(base, archive)
    if (
        type(baseline) is not dict
        or set(baseline) != {"schema_version", "root", "views", "inventories", "immutable_id"}
        or baseline["schema_version"] != "aisle.conformance-view-baseline.v1"
        or baseline["views"] != {arm: str(view) for arm, view in views.items()}
        or set(baseline["inventories"]) != set(views)
        or hashlib.sha256(
            json.dumps(
                {key: value for key, value in baseline.items() if key != "immutable_id"},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        != baseline["immutable_id"]
    ):
        raise ValueError("prepared view baseline binding differs")
    pristine = Path(baseline["root"])
    if (
        pristine.resolve() != pristine
        or not pristine.is_dir()
        or pristine == base / "retained"
        or not pristine.is_relative_to(base / "retained")
        or archive.is_relative_to(pristine)
        or pristine.is_relative_to(archive)
    ):
        raise ValueError("prepared view baseline path is invalid")
    for arm in views:
        if _run_view_inventory(pristine / arm) != baseline["inventories"][arm]:
            raise ValueError("prepared view baseline changed")
    archive.mkdir(parents=True)
    for arm in views:
        staged = archive / "staged" / arm
        shutil.copytree(pristine / arm, staged)
        if _run_view_inventory(staged) != baseline["inventories"][arm]:
            raise ValueError("prepared baseline changed while staging")
    (archive / "consumed").mkdir()
    for arm, view in views.items():
        view.rename(archive / "consumed" / arm)
        (archive / "staged" / arm).rename(view)


def archive_run_state(base, setup, archive):
    """Reset declared private state after a terminal run, retaining its old bytes.

    The caller must finish and reap the prior session first. Controller source,
    authored views, installed runtime and session receipts remain in place.
    """
    from aisle.harness.treatment_ambient import verify_declared_environment

    base, archive = Path(base).resolve(strict=True), Path(archive).absolute()
    retained = base / "retained"
    if archive.exists() or archive.is_symlink() or archive.resolve() != archive:
        raise ValueError("run state archive must be fresh and canonical")
    if archive == retained or not archive.is_relative_to(retained):
        raise ValueError("run state archive must be inside retained evidence")
    workers = [row for group in setup["worker_preparations"]["typed"] for row in group.values()]
    workers.extend(setup["worker_preparations"]["monolithic"])
    participants = [
        {"environment": row["environment"], "environment_record": row["record"]}
        for row in setup["ambient"].values()
    ]
    bindings = [
        *participants,
        setup["typed_validation"],
        *setup["run_controller"].values(),
        *workers,
    ]
    directories = {}
    for binding in bindings:
        environment, record = binding["environment"], binding["environment_record"]
        verify_declared_environment(environment, record)
        home = Path(environment["HOME"])
        children = [Path(p) for p in record["state_directories"]]
        if any(not p.is_relative_to(home) or p.resolve() != p for p in children):
            raise ValueError("private state directories escape declared HOME")
        directories[home] = children
    for worker in workers:
        directories[Path(worker["bundle"])] = []
    directories[Path(setup["typed_validation"]["snapshot_storage"])] = []
    protected = [
        Path(setup["root"]),
        *map(Path, setup["views"].values()),
        retained,
        *map(Path, setup["tool_runtime"]["trees"]),
    ]
    for path in directories:
        if (
            path == base
            or not path.is_relative_to(base)
            or path.resolve() != path
            or not path.is_dir()
            or any(path.is_relative_to(p) or p.is_relative_to(path) for p in protected)
        ):
            raise ValueError("run state overlaps protected or redirected paths")
        if any(
            other != path and (path.is_relative_to(other) or other.is_relative_to(path))
            for other in directories
        ):
            raise ValueError("run state reservations overlap")
    archive.mkdir(parents=True)
    for path, children in directories.items():
        destination = archive / path.relative_to(base)
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
        path.mkdir(mode=0o700)
        for child in children:
            child.mkdir(parents=True, exist_ok=True, mode=0o700)
