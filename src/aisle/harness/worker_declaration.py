"""Provision fresh worker grants from admitted identities and actual observations."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from aisle.harness.matched_runtime import verify_runtime
from aisle.harness.treatment_ambient import build_declared_environment
from aisle.harness.treatment_confinement import SANDBOX_EXEC, MacOSPolicy, compile_macos_profile
from aisle.harness.typed_snapshot import _read
from aisle.harness.worker_capability import audit_worker_capability


def provision_worker_declaration(
    *,
    arm,
    bundle,
    home,
    evidence,
    hidden_roots,
    runtime_record,
    python,
    python_sha256,
    adapter_sha256,
    timeout_s,
    max_calls=100000,
    max_handles=1024,
):
    """Reserve one fresh worker; downstream preparation still owns source capture.

    Partial failed reservations remain for diagnosis and cannot be silently reused.
    The returned capability report is synthetic and unscored, never study approval.
    """
    if arm not in {"typed", "monolithic"}:
        raise ValueError("worker declaration arm is unsupported")
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("worker declaration timeout must be finite and positive")
    if any(type(n) is not int or n <= 0 for n in (max_calls, max_handles)):
        raise ValueError("worker declaration resource limits must be positive integers")
    bundle, home, evidence = (Path(p).absolute() for p in (bundle, home, evidence))
    roots = (bundle, home, evidence)
    if any(p.resolve() != p or p.exists() or p.is_symlink() for p in roots):
        raise ValueError("worker declaration requires fresh canonical roots")
    if any(
        a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(roots) for b in roots[:i]
    ):
        raise ValueError("worker declaration roots overlap")
    hidden = tuple(dict.fromkeys(Path(p).absolute() for p in hidden_roots))
    if not hidden or any(p == Path("/") or p.resolve() != p or not p.is_dir() for p in hidden):
        raise ValueError("worker declaration hidden roots must be canonical existing directories")
    runtime = copy.deepcopy(runtime_record)
    verify_runtime(runtime)
    runtime_roots = tuple(Path(p) for p in runtime["trees"])
    python = Path(python).resolve(strict=True)
    if not any(python.is_relative_to(p) for p in runtime_roots):
        raise ValueError("worker interpreter is outside admitted runtime")
    if hashlib.sha256(_read(python.parent, python.name)).hexdigest() != python_sha256:
        raise ValueError("worker interpreter differs from admitted identity")
    if hashlib.sha256(_read(SANDBOX_EXEC.parent, SANDBOX_EXEC.name)).hexdigest() != adapter_sha256:
        raise ValueError("worker adapter differs from admitted identity")
    if any(
        a.is_relative_to(b) or b.is_relative_to(a)
        for a in (bundle, home)
        for b in (*hidden, *runtime_roots)
    ):
        raise ValueError("worker declaration grants overlap protected roots")
    if any(evidence.is_relative_to(p) or p.is_relative_to(evidence) for p in runtime_roots):
        raise ValueError("worker declaration evidence overlaps runtime")
    evidence.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema_version": "aisle.worker-provisioning.v1",
        "ok": False,
        "arm": arm,
        "bundle": str(bundle),
        "home": str(home),
        "error": None,
    }
    try:
        bundle.mkdir(parents=True, exist_ok=False)
        environment, environment_record = build_declared_environment(home, source_env={})
        policy = MacOSPolicy(
            visible_roots=(bundle,),
            output_roots=(home,),
            runtime_read_roots=runtime_roots,
            allowed_executables=(python,),
            hidden_roots=tuple(dict.fromkeys((*hidden, evidence))),
            network_policy="deny-external",
        )
        profile = evidence / "worker.sb"
        profile.write_text(compile_macos_profile(policy).text)
        profile.chmod(0o444)
        attestation = audit_worker_capability(
            policy=policy,
            profile_path=profile,
            python=python,
            environment=environment,
            environment_record=environment_record,
            output=evidence / "capability",
        )
        if not attestation["capability_pass"]:
            raise ValueError("actual worker capability audit failed")
        if attestation["adapter"]["sha256"] != adapter_sha256:
            raise ValueError("observed worker adapter differs from admitted identity")
        verify_runtime(runtime)
        if any(bundle.iterdir()):
            raise ValueError("worker probe left bundle reservation nonempty")
        launch = {
            "bundle": str(bundle),
            "policy": policy.canonical_dict(),
            "profile_path": str(profile),
            "attestation": attestation,
            "python": str(python),
            "python_sha256": python_sha256,
            "environment": environment,
            "environment_record": environment_record,
            "runtime_record": runtime,
            "timeout_s": timeout_s,
        }
        if arm == "monolithic":
            launch.update(max_primitive_calls=max_calls, max_handles=max_handles)
        else:
            launch["max_calls"] = max_calls
        destination = evidence / "declaration.json"
        destination.write_text(json.dumps(launch, sort_keys=True, allow_nan=False))
        destination.chmod(0o444)
        receipt["ok"] = True
        receipt["declaration_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        return launch
    except BaseException as exc:
        receipt["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        (evidence / "provisioning.json").write_text(json.dumps(receipt, allow_nan=False))
