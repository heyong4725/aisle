"""Controller-owned arm tool attempts for unscored engineering sessions."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from aisle.harness.matched_session import AdmissionError, _digest, verify_active_plan, verify_plan
from aisle.harness.treatment_ambient import spawn_isolated_process
from aisle.harness.treatment_confinement import (
    MacOSPolicy,
    compile_macos_profile,
    wrap_verified_command,
)


class ToolController:
    """Serialize real tool calls and retain their controller observations.

    Inputs belong to the session controller. Participant requests select only
    supported operations; they cannot supply argv, roots, policies or budgets.
    """

    def __init__(
        self,
        plan,
        root,
        views,
        arm,
        output,
        *,
        session_id,
        python,
        profile_path,
        attestation,
        monotonic=time.monotonic,
        now=None,
        create_output=False,
        worker_preparations=None,
    ):
        if worker_preparations is not None and type(worker_preparations) is not list:
            raise AdmissionError("worker preparations must be a controller-owned per-run list")
        self.worker_preparations = copy.deepcopy(worker_preparations)
        self.plan = copy.deepcopy(plan)
        self.root, self.views, self.arm = Path(root), dict(views), arm
        self.output = Path(output).resolve()
        self.session_id, self.python = session_id, Path(python).absolute()
        self.profile_path, self.attestation = Path(profile_path), copy.deepcopy(attestation)
        self.clock = monotonic
        self.now = now or (lambda: datetime.now(UTC).isoformat())
        self.attempts, self.wall_spent = 0, 0.0
        self.runs_reserved, self.episodes_reserved = 0, 0
        self.lock = threading.Lock()
        self._typed_storage_owner = None
        protected = [Path(view).resolve() for view in views.values()]
        protected.extend(
            Path(binding["scratch"]).resolve()
            for binding in plan.get("confinement_bindings", {}).values()
        )
        if any(
            self.output.is_relative_to(path) or path.is_relative_to(self.output)
            for path in protected
        ):
            raise AdmissionError("tool evidence overlaps participant authority")
        # Reserve one controller journal. Existing evidence cannot be resumed.
        if create_output:
            self.output.mkdir(parents=True, exist_ok=False)
        with (self.output / "tool-events.jsonl").open("x"):
            pass
        if "run_controller" in self.plan or "typed_validation" in self.plan:
            verify_plan(self.plan, self.root, self.views)
        if self.arm == "typed" and "typed_validation" in self.plan:
            self._claim_typed_storage(self.plan["typed_validation"])

    def _event(self, event):
        with (self.output / "tool-events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def check(self) -> dict:
        """Invoke the actual arm check without adding undeclared tool authority."""
        return self._dispatch("check")

    def run(self) -> dict:
        """Invoke the registered arm launcher with the sealed development protocol."""
        return self._dispatch("run", use_preparations=self.worker_preparations is not None)

    def run_prepared(self, config_path, config_sha256):
        """Trusted controller API; participant tool requests cannot supply these inputs."""
        return self._dispatch("run", prepared_run=(config_path, config_sha256))

    def run_with_launch(self, launch):
        """Trusted API: seal prepared worker selections inside the reserved attempt."""
        return self._dispatch("run", prepared_launch=copy.deepcopy(launch), construct_run=True)

    def run_with_workers(self, declarations):
        """Trusted API: prepare current authored sources using supplied worker grants."""
        return self._dispatch(
            "run", worker_declarations=copy.deepcopy(declarations), prepare_workers=True
        )

    def _provider_template(self, current, output, template):
        provider = copy.deepcopy(template)
        limits = ("max_calls",) if self.arm == "typed" else ("max_primitive_calls", "max_handles")
        if type(provider) is not dict or set(provider) != {"allocation_root", "timeout_s", *limits}:
            raise AdmissionError(f"{self.arm} provider requires exact allocation and budget fields")
        timeout = provider["timeout_s"]
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
            or any(type(provider[key]) is not int or provider[key] <= 0 for key in limits)
        ):
            raise AdmissionError(f"{self.arm} provider requires positive finite worker budgets")
        if type(provider["allocation_root"]) is not str:
            raise AdmissionError(f"{self.arm} provider allocation must be a path string")
        allocation = Path(provider["allocation_root"])
        protected = [
            self.root,
            *self.views.values(),
            output,
            *(Path(p) for p in current["tool_runtime"]["trees"]),
            *(
                Path(binding["environment_record"]["home"])
                for binding in current["run_controller"].values()
            ),
        ]
        if "typed_validation" in current:
            protected.append(Path(current["typed_validation"]["snapshot_storage"]))
        for binding in current["confinement_bindings"].values():
            for key in ("visible_roots", "output_roots", "runtime_read_roots"):
                protected.extend(Path(p) for p in binding["policy"][key])
        if (
            not allocation.is_absolute()
            or allocation.resolve() != allocation
            or allocation.exists()
            or allocation.is_symlink()
            or any(allocation.is_relative_to(p) or p.is_relative_to(allocation) for p in protected)
        ):
            raise AdmissionError(
                f"{self.arm} provider allocation is not fresh or overlaps protected state"
            )
        return provider

    def _prepare_typed_run(self, current, output, record, started, wall, declarations):
        from aisle.harness.typed_run_prepare import prepare_typed_stages

        if (
            self.arm != "typed"
            or "typed_validation" not in current
            or "run_controller" not in current
        ):
            raise AdmissionError(
                "typed run preparation requires admitted validation/controller bindings"
            )
        dynamic = type(declarations) is dict and set(declarations) == {"provider"}
        if dynamic:
            provider = self._provider_template(current, output, declarations["provider"])
        elif not isinstance(declarations, list) or not declarations:
            raise AdmissionError("typed run preparation requires worker declarations")
        inputs = self._typed_check(current, output, record, started, wall)
        record["preparation"] = {
            "validation": {
                key: record.get(key)
                for key in ("ok", "classification", "process", "result", "error")
            },
            "snapshot_id": inputs["snapshot_record"]["immutable_id"],
        }
        if not record["ok"]:
            return None
        record.update(
            ok=False,
            classification="infrastructure_exclusion",
            process=None,
            result=None,
            error=None,
        )
        if dynamic:
            return {
                "provider": {
                    **provider,
                    "snapshot": str(inputs["snapshot"]),
                    "snapshot_record": inputs["snapshot_record"],
                    "validation_output": str(inputs["validation_output"]),
                }
            }
        return prepare_typed_stages(
            controller_root=self.root,
            **inputs,
            declarations=declarations,
            output=output / "typed-run",
            runtime_record=current["tool_runtime"],
            adapter_sha256=current["arms"][self.arm]["confinement"]["adapter_binary_sha256"],
            protected_roots=[
                self.root,
                *self.views.values(),
                current["typed_validation"]["snapshot_storage"],
                output,
            ],
        )

    def _prepare_monolithic_run(self, current, output, record, declaration):
        from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
        from aisle.harness.typed_snapshot import _read

        if "run_controller" not in current or "tool_runtime" not in current:
            raise AdmissionError("monolithic preparation requires controller/runtime bindings")
        try:
            if type(declaration) is dict and set(declaration) == {"provider"}:
                provider = self._provider_template(current, output, declaration["provider"])
                module = self.views["monolithic"] / "experts/monolithic/expert_t1.py"
                source = _read(module.parent, module.name)
                destination = output / "monolithic-input/module.py"
                destination.parent.mkdir(parents=True, exist_ok=False)
                with destination.open("xb") as stream:
                    stream.write(source)
                destination.chmod(0o444)
                return {
                    "provider": {**provider, "module_sha256": hashlib.sha256(source).hexdigest()}
                }
            return prepare_monolithic_run(
                controller_root=self.root,
                views=self.views,
                output=output,
                declaration=declaration,
                runtime=current["tool_runtime"],
                adapter=current["arms"][self.arm]["confinement"]["adapter_binary_sha256"],
                embodiment=current["development"]["embodiment"],
            )
        finally:
            for name in ("module.py", "worker-config.json"):
                path = output / "monolithic-input" / name
                if path.is_file() and not path.is_symlink():
                    record["artifacts"]["monolithic-input/" + name] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()

    def _run_context(self, current, record):
        return {
            "session_id": self.session_id,
            "plan_id": self.plan["immutable_id"],
            "run_id": record["run_id"],
            "arm": self.arm,
            "controller_root": str(self.root),
            "participant_root": str(self.views[self.arm]),
            "development": current["development"],
            "runtime_record": current["tool_runtime"],
            "worker_adapter_sha256": current["arms"][self.arm]["confinement"][
                "adapter_binary_sha256"
            ],
        }

    def _seal_run_launch(self, current, output, record, launch):
        if "run_controller" not in current or "tool_runtime" not in current:
            raise AdmissionError("run construction requires admitted controller/runtime bindings")
        expected_fields = (
            ({"stages"}, {"provider"})
            if self.arm == "typed"
            else ({"worker_config", "worker_config_sha256"}, {"provider"})
        )
        if type(launch) is not dict or set(launch) not in expected_fields:
            raise AdmissionError("prepared worker selections do not match the active arm")
        path = output / "run-config.json"
        for binding in current["confinement_bindings"].values():
            for key in ("visible_roots", "output_roots", "runtime_read_roots"):
                if any(path.is_relative_to(Path(p).resolve()) for p in binding["policy"][key]):
                    raise AdmissionError("run configuration overlaps participant authority")
        config = {
            **self._run_context(current, record),
            "schema_version": "aisle.matched-run-config.v1",
            "purpose": "expert_parity",
            "launch": launch,
        }
        data = json.dumps(config, sort_keys=True, allow_nan=False).encode()
        if len(data) > 16 * 1024 * 1024:
            raise AdmissionError("run configuration exceeds size limit")
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o444)
        return path, hashlib.sha256(data).hexdigest()

    def _prepared_run(self, current, output, record, started, wall, prepared):
        from aisle.harness.matched_run_launch import launch_configured_run

        if prepared is None or "run_controller" not in current or "tool_runtime" not in current:
            raise AdmissionError("prepared run requires admitted controller and runtime bindings")
        config_path = Path(prepared[0]).absolute()
        if config_path.resolve() != config_path or not config_path.is_file():
            raise AdmissionError("prepared run configuration is missing or redirected")
        with config_path.open("rb") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise AdmissionError("prepared run configuration exceeds size limit")
        if hashlib.sha256(data).hexdigest() != prepared[1]:
            raise AdmissionError("prepared run configuration hash differs")
        retained_config = output / "run-config.json"
        if config_path != retained_config:
            with retained_config.open("xb") as stream:
                stream.write(data)
            retained_config.chmod(0o444)
        record["run_controller"] = {
            "config_path": str(retained_config),
            "config_sha256": prepared[1],
        }
        child_output = output / "run-controller"
        expected = self._run_context(current, record)
        # The child's rollout has its own execution timeout. Its enclosing
        # transaction also provisions workers and retains evidence after rollout
        # shutdown; cap that entire transaction by the unspent tool budget.
        remaining = wall - self.wall_spent - (self.clock() - started)
        try:
            child = launch_configured_run(
                binding=current["run_controller"][self.arm],
                runtime_record=current["tool_runtime"],
                source_roots=[self.root, *self.views.values()],
                participant_policies=[
                    value["policy"] for value in current["confinement_bindings"].values()
                ],
                config_path=retained_config,
                config_sha256=prepared[1],
                expected=expected,
                output=child_output,
                timeout_s=remaining,
            )
            record.update({key: child.get(key) for key in ("process", "result", "error")})
            result = child.get("result") or {}
            record["classification"] = (
                "infrastructure_exclusion"
                if child.get("error") or not result or result.get("infrastructure_invalid")
                else "tool_result"
            )
            record["ok"] = record["classification"] == "tool_result" and child["ok"]
            verify_active_plan(self.plan, self.root, self.views, self.arm)
        finally:
            if child_output.is_dir():
                terminal = child_output / "process.json"
                if terminal.is_file() and not terminal.is_symlink():
                    record["process"] = json.loads(terminal.read_text()).get("process")
                for path in child_output.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        name = path.relative_to(child_output).as_posix()
                        record["artifacts"]["run-controller/" + name] = hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest()
                        if path.parent == child_output and path.name in {
                            "stdout.json",
                            "stderr.log",
                            "invocation.json",
                        }:
                            (output / path.name).write_bytes(path.read_bytes())

    def _claim_typed_storage(self, binding):
        """Reserve controller-owned validation state before a frontend can start."""
        storage = Path(binding["snapshot_storage"])
        owner_path = storage / "session.json"
        owner = {"session_id": self.session_id, "plan_id": self.plan["immutable_id"]}
        if self._typed_storage_owner is None:
            if any(storage.iterdir()):
                raise AdmissionError("validation snapshot storage is not fresh; resume refused")
            with owner_path.open("x") as stream:
                stream.write(json.dumps(owner, sort_keys=True) + "\n")
            owner_path.chmod(0o444)
            self._typed_storage_owner = owner
        if (
            owner_path.is_symlink()
            or self._typed_storage_owner != owner
            or json.loads(owner_path.read_text()) != owner
        ):
            raise AdmissionError("validation snapshot storage owner has drifted")
        return storage

    def _typed_check(self, current, output, record, started, wall):
        from aisle.harness.typed_snapshot import (
            archive_typed_snapshot,
            build_typed_validation_snapshot,
        )
        from aisle.harness.typed_validation import run_validation, verify_validation_binding

        binding = current["typed_validation"]
        kwargs = verify_validation_binding(
            binding,
            current["tool_runtime"],
            [self.root, *self.views.values()],
            [item["policy"] for item in current["confinement_bindings"].values()],
        )
        storage = self._claim_typed_storage(binding)
        snapshot = (
            storage
            / _digest(
                {
                    "session": self.session_id,
                    "plan": current["immutable_id"],
                    "attempt": self.attempts,
                }
            ).split(":")[1]
        )
        receipt = build_typed_validation_snapshot(self.root, self.views["typed"], snapshot)
        validation_output = output / "validation"
        try:
            result = run_validation(
                **kwargs,
                snapshot=snapshot,
                snapshot_record=receipt,
                embodiment="franka",
                output=validation_output,
                timeout_s=wall - self.wall_spent - (self.clock() - started),
            )
            verify_active_plan(self.plan, self.root, self.views, self.arm)
            record.update(
                {
                    key: result.get(key)
                    for key in (
                        "process",
                        "result",
                        "classification",
                        "ok",
                        "error",
                    )
                }
            )
        finally:
            # Preserve the normal journal files as well as the bound launch evidence,
            # including a terminal record written before cancellation propagates.
            for name in ("stdout.json", "stderr.log", "profile.sb"):
                source = validation_output / name
                if source.is_file():
                    (output / name).write_bytes(source.read_bytes())
            launch = validation_output / "launch.json"
            if launch.is_file():
                (output / "invocation.json").write_bytes(launch.read_bytes())
            terminal = validation_output / "result.json"
            if terminal.is_file():
                retained = json.loads(terminal.read_text())
                record["process"] = retained.get("process")
            if validation_output.is_dir():
                for path in validation_output.iterdir():
                    if path.is_file() and not path.is_symlink():
                        record["artifacts"]["validation/" + path.name] = hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest()

            try:
                archive = archive_typed_snapshot(snapshot, receipt, output / "source-snapshot")
                record["snapshot_archive"] = archive
                record["artifacts"].update(
                    {"source-snapshot/" + name: digest for name, digest in archive["files"].items()}
                )
            except Exception as exc:
                record.update(ok=False, classification="infrastructure_exclusion", error=str(exc))
                record["snapshot_archive"] = {"error": str(exc)}
        return {
            "snapshot": snapshot,
            "snapshot_record": receipt,
            "validation_output": validation_output,
        }

    def _dispatch(
        self,
        operation: str,
        prepared_run=None,
        prepared_launch=None,
        construct_run=False,
        worker_declarations=None,
        prepare_workers=False,
        use_preparations=False,
    ) -> dict:
        with self.lock:
            self.attempts += 1
            attempt = self.attempts
            output = self.output / f"tool-{attempt:06d}"
            output.mkdir()
            record = {
                "schema_version": "aisle.matched-tool-attempt.v1",
                "session_id": self.session_id,
                "plan_id": self.plan["immutable_id"],
                "arm": self.arm,
                "attempt": attempt,
                "operation": operation,
                "development_id": None,
                "run_id": None,
                "run_evidence": None,
                "reservation": {"runs": 0, "episodes": 0},
                "classification": "infrastructure_exclusion",
                "ok": False,
                "eligible_for_estimate": False,
                "process": None,
                "result": None,
                "error": None,
                "artifacts": {},
                "lifecycle": {"started_at": self.now(), "finished_at": None},
            }
            self._event(
                {
                    "event": "started",
                    **{
                        key: record[key]
                        for key in ("session_id", "plan_id", "arm", "attempt", "operation")
                    },
                }
            )
            started = self.clock()
            source_run = None
            development = None
            try:
                current = verify_active_plan(self.plan, self.root, self.views, self.arm)
                manifest = current["arms"][self.arm]
                budget = manifest["budget"]
                ceiling, wall = budget.get("tool_ceiling"), budget.get("tool_wall_ceiling_s")
                if type(ceiling) is not int or ceiling <= 0 or attempt > ceiling:
                    raise AdmissionError("tool call budget is absent or exhausted")
                if (
                    type(wall) not in (int, float)
                    or not math.isfinite(wall)
                    or wall <= self.wall_spent
                ):
                    raise AdmissionError("tool wall budget is absent or exhausted")
                if f"harness.{operation}" not in manifest["policy"]["allowed_external_tools"]:
                    raise AdmissionError(f"harness.{operation} is not an admitted tool")
                if operation == "run":
                    development = current.get("development")
                    if development is None:
                        raise AdmissionError("development protocol is not admitted")
                    if (
                        self.runs_reserved >= development["run_ceiling"]
                        or self.episodes_reserved + len(development["seeds"])
                        > development["episode_ceiling"]
                    ):
                        raise AdmissionError("development run budget exhausted")
                    record["development_id"] = _digest(development)
                    record["run_id"] = (
                        "matched-"
                        + _digest(
                            {
                                "session": self.session_id,
                                "plan": self.plan["immutable_id"],
                                "arm": self.arm,
                                "attempt": attempt,
                            }
                        ).split(":")[1][:24]
                    )
                    runs_root = self.root / "runs"
                    if runs_root.resolve() != runs_root.absolute() or (
                        runs_root.exists() and not runs_root.is_dir()
                    ):
                        raise AdmissionError("development runs root is redirected or invalid")
                    source_run = runs_root / record["run_id"]
                    if source_run.exists() or source_run.is_symlink():
                        raise AdmissionError("development run already exists; resume refused")
                    self.runs_reserved += 1
                    self.episodes_reserved += len(development["seeds"])
                    record["reservation"] = {"runs": 1, "episodes": len(development["seeds"])}
                if operation == "run" and use_preparations:
                    index = self.runs_reserved - 1
                    if index >= len(self.worker_preparations):
                        raise AdmissionError("no worker preparation remains for the reserved run")
                    worker_declarations = copy.deepcopy(self.worker_preparations[index])
                    data = json.dumps(worker_declarations, sort_keys=True, allow_nan=False).encode()
                    path = output / "worker-declaration.json"
                    with path.open("xb") as stream:
                        stream.write(data)
                    path.chmod(0o444)
                    record["worker_preparation"] = {
                        "index": index,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    prepare_workers = True
                if operation == "run" and prepare_workers:
                    # Validation has a separate process; it is not a development run.
                    reserved_source_run = source_run
                    source_run = None
                    if self.arm == "typed":
                        prepared_launch = self._prepare_typed_run(
                            current, output, record, started, wall, worker_declarations
                        )
                    else:
                        prepared_launch = self._prepare_monolithic_run(
                            current, output, record, worker_declarations
                        )
                    if prepared_launch is None:
                        return record
                    source_run = reserved_source_run
                    construct_run = True
                if operation == "run" and construct_run:
                    prepared_run = self._seal_run_launch(current, output, record, prepared_launch)
                if operation == "run" and (prepared_run is not None or "run_controller" in current):
                    self._prepared_run(current, output, record, started, wall, prepared_run)
                    return record
                if operation == "check" and self.arm == "typed":
                    if "typed_validation" in current:
                        self._typed_check(current, output, record, started, wall)
                        return record
                    if "tool_runtime" in current:
                        raise AdmissionError(
                            "bound typed runtime requires a validation launch binding"
                        )
                declared = current["confinement_bindings"][self.arm]["policy"]
                policy = MacOSPolicy(
                    **{
                        key: value if key == "network_policy" else tuple(Path(p) for p in value)
                        for key, value in declared.items()
                    }
                )
                compiled = compile_macos_profile(policy)
                python_hash = hashlib.sha256(self.python.read_bytes()).hexdigest()
                if {"name": "harness-python", "sha256": python_hash} not in manifest[
                    "runtime_binaries"
                ]:
                    raise AdmissionError("tool interpreter hash is not admitted")
                if self.python.resolve() not in {
                    path.resolve() for path in policy.allowed_executables
                }:
                    raise AdmissionError("tool interpreter has no execution grant")
                if (
                    self.attestation.get("adapter", {}).get("sha256")
                    != manifest["confinement"]["adapter_binary_sha256"]
                ):
                    raise AdmissionError("tool adapter differs from admitted adapter")
                view = Path(self.views[self.arm])
                if operation == "run":
                    common = [
                        "--root",
                        str(self.root),
                        "--tier",
                        development["tier"],
                        "--embodiment",
                        development["embodiment"],
                        "--episodes",
                        str(len(development["seeds"])),
                        "--seeds",
                        ",".join(str(seed) for seed in development["seeds"]),
                        "--run-id",
                        record["run_id"],
                        "--timeout-s",
                        str(development["timeout_s"]),
                    ]
                    if self.arm == "typed":
                        args = [
                            "rollout",
                            "--graph",
                            str(view / "graphs/expert_t1.yaml"),
                            "--reset",
                            development["reset"],
                            "--verifier",
                            development["verifier"],
                            *common,
                        ]
                    else:
                        args = [
                            "monolith",
                            "run",
                            "--module",
                            str(view / "experts/monolithic/expert_t1.py"),
                            *common,
                        ]
                elif self.arm == "typed":
                    args = [
                        "validate",
                        str(view / "graphs/expert_t1.yaml"),
                        "--root",
                        str(view),
                        "--embodiment",
                        "franka",
                    ]
                else:
                    args = [
                        "monolith",
                        "check",
                        "--module",
                        str(view / "experts/monolithic/expert_t1.py"),
                        "--embodiment",
                        "franka",
                    ]
                command = [str(self.python), "-B", "-m", "aisle.harness.cli", *args]
                retained_profile = output / "profile.sb"
                retained_profile.write_bytes(self.profile_path.read_bytes())
                wrapped = wrap_verified_command(
                    command, compiled, retained_profile, self.attestation
                )
                (output / "invocation.json").write_text(
                    json.dumps(
                        {
                            "argv": command,
                            "wrapped_argv": wrapped,
                            "cwd": str(view),
                            "budget": budget,
                        },
                        indent=2,
                    )
                )
                ambient = current["ambient_bindings"][self.arm]
                remaining = wall - self.wall_spent - (self.clock() - started)
                if remaining <= 0:
                    raise AdmissionError("tool wall budget exhausted before process start")
                with (
                    (output / "stdout.json").open("wb") as stdout,
                    (output / "stderr.log").open("wb") as stderr,
                ):
                    process = spawn_isolated_process(
                        wrapped,
                        cwd=view,
                        environment=ambient["environment"],
                        environment_record=ambient["record"],
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                    )
                    try:
                        rc = process.wait(timeout=remaining)
                    except BaseException as exc:
                        # Stop and reap before retaining run evidence or closing
                        # its streams, including cancellation and wait failures.
                        from aisle.harness.matched_run_launch import _terminate_owned_run

                        record["cleanup"] = _terminate_owned_run(process)
                        timed_out = isinstance(exc, subprocess.TimeoutExpired)
                        record["process"] = {"rc": process.returncode, "timed_out": timed_out}
                        if timed_out:
                            raise AdmissionError("tool wall budget exhausted") from None
                        raise
                record["process"] = {"rc": rc, "timed_out": False}
                result = json.loads((output / "stdout.json").read_text())
                json.dumps(result, allow_nan=False)
                if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                    raise AdmissionError("tool returned an invalid JSON verdict")
                if (rc == 0) != result["ok"] or rc not in (0, 1):
                    raise AdmissionError("tool exit status disagrees with its verdict")
                record["result"] = result
                verify_active_plan(self.plan, self.root, self.views, self.arm)
                record["classification"] = (
                    "infrastructure_exclusion"
                    if result.get("infrastructure_invalid")
                    else "tool_result"
                )
                record["ok"] = record["classification"] == "tool_result" and result["ok"]
            except BaseException as exc:
                record["classification"] = "infrastructure_exclusion"
                record["ok"] = False
                record["error"] = str(exc) or type(exc).__name__
                if not isinstance(exc, Exception):
                    raise
            finally:
                collection_interruption = None
                # Collect after the child stops even when it timed out, crashed,
                # or never emitted a parseable verdict. Pre-launch refusals must
                # not adopt evidence from an existing run.
                if source_run is not None and record["process"] is not None:
                    try:
                        from aisle.harness.matched_collection import retain_run_bounded

                        if source_run.exists() or source_run.is_symlink():
                            collection = retain_run_bounded(
                                source_run,
                                output / "run",
                                run_id=record["run_id"],
                                timeout_s=wall - self.wall_spent - (self.clock() - started),
                            )
                            record["run_evidence"] = collection
                            if not collection["ok"]:
                                raise AdmissionError(
                                    f"development run evidence invalid: {collection['error']}"
                                )
                            if any(
                                collection["manifest"].get(key) != development[key]
                                for key in ("seeds", "tier", "verifier")
                            ):
                                raise AdmissionError(
                                    "development run evidence differs from admitted protocol"
                                )
                        elif record["result"] is not None and record["result"]["ok"]:
                            raise AdmissionError(
                                "successful development run has no retained run directory"
                            )
                    except BaseException as exc:
                        record["classification"] = "infrastructure_exclusion"
                        record["ok"] = False
                        record["error"] = "; ".join(
                            message
                            for message in (record["error"], str(exc) or type(exc).__name__)
                            if message
                        )
                        if not isinstance(exc, Exception):
                            collection_interruption = exc
                    finally:
                        collector = output / "run-collector"
                        if (collector / "process.json").is_file():
                            record["collection_process"] = "run-collector/process.json"
                            for name in (
                                "process.json",
                                "invocation.json",
                                "stdout.log",
                                "stderr.log",
                            ):
                                path = collector / name
                                if path.is_file() and not path.is_symlink():
                                    record["artifacts"]["run-collector/" + name] = hashlib.sha256(
                                        path.read_bytes()
                                    ).hexdigest()
                elapsed = self.clock() - started
                self.wall_spent += elapsed
                record["wall_s"] = elapsed
                record["lifecycle"]["finished_at"] = self.now()
                for path in sorted(output.iterdir()):
                    if path.is_file() and not path.is_symlink():
                        record["artifacts"][path.name] = hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest()
                record["immutable_id"] = _digest(record)
                (output / "attempt.json").write_text(
                    json.dumps(record, indent=2, allow_nan=False) + "\n"
                )
                self._event({"event": "finished", "record": record})
                if collection_interruption is not None:
                    raise collection_interruption
            return record
