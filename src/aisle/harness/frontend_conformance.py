"""Bound frontend profile inputs; source identity is distinct from qualification."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from itertools import chain
from pathlib import Path, PurePosixPath

ROUTE_UNITS = {
    "harness": "controller_attempt",
    "native": "client_tool_call",
    "native_edit": "client_tool_call",
    "continued_input": "client_tool_call",
    "mcp": "client_tool_call",
    "nested": "nested_tool_call",
    "subagents": "descendant_tool_call",
    "hosted": "hosted_tool_call",
}
# A collection includes both arms, route outcomes and fault cases. Retain the
# smaller per-file/per-session bound independently of the aggregate allowance.
MAX_PROFILE_BYTES = 64 * 1024 * 1024
MAX_COLLECTION_BYTES = 16 * 1024 * 1024 * 1024
MAX_COLLECTION_FILES = 262144
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024 * 1024
MAX_SESSION_FILES = 65536
EXECUTION_FIELDS = (
    "development",
    "tool_runtime",
    "typed_validation",
    "run_controller",
    "confinement_bindings",
    "ambient_bindings",
    "private_roots",
    "prompt_row",
)
FAULT_CASES = (
    "controller_unavailable",
    "controller_timeout",
    "malformed_response",
    "denial",
    "hook_absent",
    "hook_changed",
    "provider_replay",
    "concurrent_nested",
    "cancel_before_forward",
    "cancel_after_forward",
)
FAILING_HOOK = (
    'hooks.PreToolUse=[{matcher="Bash",hooks=[{type="command",timeout=1,command="exit 1"}]}]'
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def configuration_digest(candidate, launch):
    """Bind logical launch inputs; the profile pointer and coverage requirement are not inputs."""
    configuration = {
        key: copy.deepcopy(candidate[key])
        for key in (
            "agent",
            "model",
            "sampling",
            "prompts",
            "policy",
            "environment",
            "state",
            "runtime_binaries",
            "budget",
        )
    }
    configuration["budget"].pop("frontend_coverage", None)
    configuration["launch"] = {
        key: value for key, value in launch.items() if key != "conformance_profile"
    }
    return hashlib.sha256(_canonical(configuration)).hexdigest()


def execution_digest(execution):
    """Bind controller, validator, runtime and isolation inputs beyond frontend argv."""
    if type(execution) is not dict:
        raise ValueError("invalid execution binding context")
    return hashlib.sha256(
        _canonical({name: execution.get(name) for name in EXECUTION_FIELDS})
    ).hexdigest()


def fault_launch(launch, fault):
    """Derive the sole allowed configuration delta for a bound fault case."""
    if fault not in FAULT_CASES:
        raise ValueError("unsupported conformance fault")
    result = copy.deepcopy(launch)
    if fault in {"hook_absent", "hook_changed"}:
        argv = result["argv"]
        if (
            type(argv) is not list
            or not argv
            or any(type(arg) is not str for arg in argv)
            or any(
                arg == "--dangerously-bypass-hook-trust"
                or "features.hooks" in arg
                or arg.startswith("hooks.")
                for arg in argv
            )
        ):
            raise ValueError("hook fault requires an unambiguous nominal hook configuration")
        if fault == "hook_absent":
            argv.extend(["-c", "features.hooks=false"])
        else:
            argv.insert(1, "--dangerously-bypass-hook-trust")
            argv.extend(["-c", "features.hooks=true", "-c", FAILING_HOOK])
    return result


def _hash(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate conformance profile field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite conformance profile value")


def _read(root, name, limit):
    if (
        type(name) is not str
        or not name
        or str(PurePosixPath(name)) != name
        or PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
    ):
        raise ValueError("invalid conformance input path")
    path = root / name
    if path.resolve() != path:
        raise ValueError("redirected conformance input")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("conformance input is not a bounded regular file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("conformance input exceeds byte budget")
    return raw


class _DiskInputs(Mapping):
    """Keep only the index resident; authenticate each bounded file on access."""

    def __init__(self, root, hashes):
        self.root = Path(root)
        self.hashes = dict(hashes)

    def __iter__(self):
        return iter(self.hashes)

    def __len__(self):
        return len(self.hashes)

    def __getitem__(self, name):
        digest = self.hashes[name]
        raw = _read(self.root, name, MAX_INPUT_BYTES)
        if not _hash(digest) or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("conformance input bytes drift")
        return raw


def _bound_document(profile_bytes, reference):
    if (
        type(reference) is not dict
        or set(reference) != {"path", "sha256"}
        or not _hash(reference["sha256"])
        or type(profile_bytes) is not bytes
        or len(profile_bytes) > MAX_PROFILE_BYTES
        or hashlib.sha256(profile_bytes).hexdigest() != reference["sha256"]
    ):
        raise ValueError("conformance profile differs from its bound reference")
    return json.loads(profile_bytes, object_pairs_hook=_object, parse_constant=_constant)


def verify_profile_inputs(
    profile_bytes, files, *, reference, candidate, launch, arm, execution=None
):
    """Recheck an acquired profile without promoting its proof declarations to verdicts."""
    from aisle.harness.matched_session import CONTROLLER_FILES

    try:
        profile = _bound_document(profile_bytes, reference)
        if (
            type(profile) is not dict
            or set(profile) - {"proof_files", "execution_sha256", "faults"}
            != {
                "schema_version",
                "frontend",
                "bindings",
                "controller_files",
                "fixture_files",
                "routes",
            }
            or profile["schema_version"] != "aisle.frontend-conformance.v1"
            or arm not in {"typed", "monolithic"}
            or profile["frontend"] != candidate["agent"]
            or set(profile["bindings"]) != {"typed", "monolithic"}
            or any(not _hash(value) for value in profile["bindings"].values())
            or profile["bindings"][arm] != configuration_digest(candidate, launch)
            or set(profile["controller_files"]) != set(CONTROLLER_FILES)
            or type(profile["fixture_files"]) is not dict
            or not 1 <= len(profile["fixture_files"]) <= 128
            or type(profile.get("proof_files", {})) is not dict
            or len(profile.get("proof_files", {})) > MAX_COLLECTION_FILES
            or type(profile["routes"]) is not dict
            or set(profile["routes"]) != set(ROUTE_UNITS)
        ):
            raise ValueError("profile identity, configuration or route inventory differs")
        if "faults" in profile and (
            type(profile["faults"]) is not dict
            or set(profile["faults"]) != set(FAULT_CASES)
            or any(type(rows) is not list or len(rows) > 128 for rows in profile["faults"].values())
        ):
            raise ValueError("invalid conformance fault inventory")
        if "execution_sha256" in profile and (
            not _hash(profile["execution_sha256"])
            or execution is not None
            and profile["execution_sha256"] != execution_digest(execution)
        ):
            raise ValueError("profile execution binding differs from target admission")
        if candidate["budget"].get("frontend_coverage") == "complete" and (
            "execution_sha256" not in profile or execution is None
        ):
            raise ValueError("complete coverage requires a target execution binding")
        for name, unit in ROUTE_UNITS.items():
            row = profile["routes"][name]
            if (
                type(row) is not dict
                or set(row) != {"counting_unit", "proofs"}
                or row["counting_unit"] != unit
                or type(row["proofs"]) is not list
                or len(row["proofs"]) > 128
            ):
                raise ValueError("invalid conformance route counting unit or evidence list")
        expected = dict(profile["controller_files"])
        for collection in (profile["fixture_files"], profile.get("proof_files", {})):
            for name, digest in collection.items():
                if name in expected and expected[name] != digest:
                    raise ValueError("conflicting conformance input hashes")
                expected[name] = digest
        if (
            type(files) not in (dict, _DiskInputs)
            or len(expected) > MAX_COLLECTION_FILES
            or set(files) != set(expected)
        ):
            raise ValueError("conformance controller or fixture bytes drift")
        total = 0
        for name, digest in expected.items():
            raw = files[name]
            if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
                raise ValueError("conformance input exceeds file budget")
            total += len(raw)
            if total > MAX_COLLECTION_BYTES:
                raise ValueError("conformance inputs exceed collection budget")
            if not _hash(digest) or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("conformance controller or fixture bytes drift")
        checked = set()
        inventories = [(None, row["proofs"]) for row in profile["routes"].values()]
        inventories.extend(profile.get("faults", {}).items())
        for fault, entries in inventories:
            seen = set()
            for proof in entries:
                if (
                    type(proof) is not dict
                    or set(proof) != {"arm", "session"}
                    or type(proof["arm"]) is not str
                    or proof["arm"] not in {"typed", "monolithic"}
                    or type(proof["session"]) is not str
                    or proof["session"] not in profile.get("proof_files", {})
                    or (proof["arm"], proof["session"]) in seen
                ):
                    raise ValueError("invalid or unbound conformance session proof")
                seen.add((proof["arm"], proof["session"]))
                key = (fault, proof["session"])
                if proof["arm"] == arm and key not in checked:
                    read_session_proof(
                        {"profile": profile, "files": files},
                        proof["session"],
                        candidate=candidate,
                        launch=launch if fault is None else fault_launch(launch, fault),
                        arm=arm,
                    )
                    checked.add(key)
        return profile
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("malformed conformance profile inputs") from exc


def verify_route_matrix(bound, *, candidate, launch, arm):
    """Require available and refused cases for every route of one exact arm binding.

    Inputs must first pass verify_profile_inputs. Each proof is independently
    acquired against the candidate, then its original launch and scenario are
    replayed. Route coverage alone does not establish the failure matrix or
    independent confinement needed for broader qualification.
    """
    from aisle.harness.frontend_qualification import route_scenario_evidence, verify_original_launch

    try:
        if arm not in {"typed", "monolithic"} or set(bound["profile"]["routes"]) != set(
            ROUTE_UNITS
        ):
            raise ValueError("invalid route matrix inventory")
        evidence, sessions = {}, set()
        operations = sorted(
            name.removeprefix("harness.")
            for name in candidate["policy"]["allowed_external_tools"]
            if name.startswith("harness.")
        )
        if not operations or any(operation not in {"check", "run"} for operation in operations):
            raise ValueError("unsupported or absent admitted harness operations")
        harness_cases = set()
        for route, unit in ROUTE_UNITS.items():
            row = bound["profile"]["routes"][route]
            if row["counting_unit"] != unit:
                raise ValueError("route matrix counting unit differs")
            cases = {}
            for entry in row["proofs"]:
                if entry["arm"] != arm:
                    continue
                proof = read_session_proof(
                    bound, entry["session"], candidate=candidate, launch=launch, arm=arm
                )
                session = proof["record"]["session_id"]
                if session in sessions:
                    raise ValueError("route matrix reuses a session")
                sessions.add(session)
                original = verify_original_launch(proof)
                result = route_scenario_evidence(proof)
                case = result["case"]
                if (
                    original["launch_verified"] is not True
                    or result["case_verified"] is not True
                    or result["route"] != route
                    or case not in {"available", "quota_refused"}
                ):
                    raise ValueError("route matrix proof does not establish its declared case")
                if route == "harness":
                    operation = result["operation"]
                    if operation not in operations:
                        raise ValueError("harness proof selects an unadmitted operation")
                    if case == "available" and result["effect"].get("ok") is not True:
                        raise ValueError(
                            "harness availability requires a successful controller result"
                        )
                    harness_cases.add((operation, case))
                cases.setdefault(case, []).append(
                    {
                        "session": entry["session"],
                        "session_id": session,
                        "launch": original,
                        "scenario": result,
                    }
                )
            evidence[route] = cases
        missing = [
            route + "/" + case
            for route, cases in evidence.items()
            for case in ("available", "quota_refused")
            if case not in cases
        ]
        missing.extend(
            "harness/" + operation + "/" + case
            for operation in operations
            for case in ("available", "quota_refused")
            if (operation, case) not in harness_cases
        )
        if missing:
            raise ValueError("unverified route cases: " + ", ".join(missing))
        return {
            "arm": arm,
            "routes_verified": True,
            "routes": evidence,
            "harness_operations": operations,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid route matrix evidence") from exc


def verify_fault_matrix(bound, *, candidate, launch, arm):
    """Replay required adapter faults, overlapping calls, and interrupted delivery boundaries."""
    from aisle.harness.frontend_qualification import (
        audit_concurrent_nested,
        audit_controller_denial,
        audit_controller_fault,
        audit_hook_independence,
        audit_provider_interruption,
        audit_provider_replay,
    )

    try:
        faults = bound["profile"].get("faults", {})
        if arm not in {"typed", "monolithic"} or set(faults) != set(FAULT_CASES):
            raise ValueError("unverified adapter fault inventory")
        evidence, used = {}, set()
        for kind in FAULT_CASES:
            evidence[kind] = []
            audit = (
                audit_provider_interruption
                if kind in {"cancel_before_forward", "cancel_after_forward"}
                else audit_provider_replay
                if kind == "provider_replay"
                else audit_concurrent_nested
                if kind == "concurrent_nested"
                else audit_controller_denial
                if kind == "denial"
                else audit_hook_independence
                if kind in {"hook_absent", "hook_changed"}
                else audit_controller_fault
            )
            for entry in faults[kind]:
                if entry["arm"] != arm:
                    continue
                proof = read_session_proof(
                    bound,
                    entry["session"],
                    candidate=candidate,
                    launch=fault_launch(launch, kind),
                    arm=arm,
                )
                session = proof["record"]["session_id"]
                if session in used:
                    raise ValueError("fault matrix reuses a session")
                used.add(session)
                result = audit(proof)
                if result["fault_verified"] is not True or result["fault"] != kind:
                    raise ValueError("fault proof differs from declared scenario")
                evidence[kind].append(
                    {"session": entry["session"], "session_id": session, "audit": result}
                )
        missing = [kind for kind in FAULT_CASES if not evidence[kind]]
        if missing:
            raise ValueError("unverified adapter fault cases: " + ", ".join(missing))
        return {
            "arm": arm,
            "adapter_faults_verified": True,
            "faults": evidence,
            "complete_coverage": False,
            "confinement_verified": False,
        }
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid adapter fault matrix") from exc


def _acquire_profile(
    profile_bytes, input_root, reference, *, candidate, launch, arm, execution=None
):
    try:
        profile = _bound_document(profile_bytes, reference)
        names = (
            set(profile["controller_files"])
            | set(profile["fixture_files"])
            | set(profile.get("proof_files", {}))
        )
        if len(names) > MAX_COLLECTION_FILES:
            raise ValueError("conformance input inventory exceeds limit")
        hashes = {
            **profile["controller_files"],
            **profile["fixture_files"],
            **profile.get("proof_files", {}),
        }
        files = _DiskInputs(input_root, hashes)
        profile = verify_profile_inputs(
            profile_bytes,
            files,
            reference=reference,
            candidate=candidate,
            launch=launch,
            arm=arm,
            execution=execution,
        )
        return {"profile": profile, "profile_bytes": profile_bytes, "files": files}
    except (OSError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("cannot acquire bound conformance profile") from exc


def read_bound_profile(root, reference, *, candidate, launch, arm, execution=None):
    """Acquire bounded controller-side inputs for shared live/offline binding checks."""
    root = Path(root).resolve()
    try:
        profile_bytes = _read(root, reference["path"], MAX_PROFILE_BYTES)
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("cannot acquire bound conformance profile") from exc
    return _acquire_profile(
        profile_bytes,
        root,
        reference,
        candidate=candidate,
        launch=launch,
        arm=arm,
        execution=execution,
    )


def retain_bound_profile(root, destination, reference, *, candidate, launch, arm, execution=None):
    """Copy verified inputs to a fresh controller-owned snapshot before execution."""
    bound = read_bound_profile(
        root, reference, candidate=candidate, launch=launch, arm=arm, execution=execution
    )
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    artifacts = chain(
        [("profile.json", bound["profile_bytes"])],
        (("inputs/" + name, raw) for name, raw in bound["files"].items()),
    )
    hashes = {}
    for name, raw in artifacts:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return hashes


def read_retained_profile(destination, reference, *, candidate, launch, arm, execution=None):
    """Replay retained bytes against the admission reference, without the source tree."""
    destination = Path(destination).absolute()
    try:
        raw = _read(destination, "profile.json", MAX_PROFILE_BYTES)
        return _acquire_profile(
            raw,
            destination / "inputs",
            reference,
            candidate=candidate,
            launch=launch,
            arm=arm,
            execution=execution,
        )
    except OSError as exc:
        raise ValueError("cannot acquire retained conformance profile") from exc


def _proof_name(name):
    if (
        type(name) is not str
        or not name
        or str(PurePosixPath(name)) != name
        or PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
    ):
        raise ValueError("invalid conformance proof path")
    return PurePosixPath(name)


def _proof_document(raw, schema):
    from aisle.harness.matched_session import _digest

    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if type(value) is not dict or value.get("schema_version") != schema:
        raise ValueError("invalid session proof schema")
    content = dict(value)
    identity = content.pop("immutable_id")
    if _digest(content) != identity:
        raise ValueError("session proof identity differs")
    return value


def _session_inventory(record):
    expected = dict(record["artifacts"])
    for phase in ("authored", "final"):
        for name, snapshot in record["snapshots"][phase].items():
            _proof_name(name)
            key = phase + "/" + name
            if key in expected and expected[key] != snapshot["sha256"]:
                raise ValueError("conflicting session snapshot identity")
            expected[key] = snapshot["sha256"]
    if len(expected) > MAX_SESSION_FILES:
        raise ValueError("session proof has too many artifacts")
    for name, digest in expected.items():
        _proof_name(name)
        if not _hash(digest):
            raise ValueError("invalid session artifact hash")
    return expected


def acquire_session_inputs(root):
    """Read exactly the authenticated receipt index, leaving other retained files alone."""
    try:
        root = Path(root).absolute()
        raw = _read(root, "matched-session.json", min(MAX_INPUT_BYTES, MAX_SESSION_BYTES))
        record = _proof_document(raw, "aisle.matched-session-evidence.v1")
        files = {"matched-session.json": raw}
        remaining = MAX_SESSION_BYTES - len(raw)
        for name, digest in _session_inventory(record).items():
            data = _read(root, name, min(MAX_INPUT_BYTES, remaining))
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("session artifact or snapshot differs from its receipt")
            if name not in files:
                remaining -= len(data)
            files[name] = data
        return files
    except (OSError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("cannot acquire indexed session inputs") from exc


def read_session_proof(bound, record_name, *, candidate, launch, arm):
    """Acquire a bound session/admission pair for semantic route qualification.

    This establishes receipt identity and its complete referenced byte inputs.
    It intentionally returns no qualification verdict: source, execution and
    refusal semantics must still be re-audited by the route qualifier.
    """
    try:
        path = _proof_name(record_name)
        if path.name != "matched-session.json":
            raise ValueError("proof must reference a matched session record")
        profile, files = bound["profile"], bound["files"]
        proofs = profile.get("proof_files", {})
        acquired_bytes = 0
        acquired = {}

        def acquire(name):
            nonlocal acquired_bytes
            _proof_name(name)
            if name in acquired:
                return acquired[name]
            raw = files[name]
            if (
                type(raw) is not bytes
                or len(raw) > MAX_INPUT_BYTES
                or name not in proofs
                or not _hash(proofs[name])
                or hashlib.sha256(raw).hexdigest() != proofs[name]
            ):
                raise ValueError("session proof bytes are not bound by the profile")
            acquired_bytes += len(raw)
            if acquired_bytes > MAX_SESSION_BYTES:
                raise ValueError("session proof exceeds byte budget")
            acquired[name] = raw
            return raw

        record = _proof_document(acquire(record_name), "aisle.matched-session-evidence.v1")
        expected = _session_inventory(record)
        artifacts = {}
        for name, digest in expected.items():
            _proof_name(name)
            raw = acquire(str(path.parent / name))
            if not _hash(digest) or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("session artifact or snapshot differs from its receipt")
            artifacts[name] = raw
        admission = _proof_document(artifacts["admission.json"], "aisle.matched-session-plan.v1")
        if (
            "execution_sha256" in profile
            and execution_digest(admission) != profile["execution_sha256"]
        ):
            raise ValueError("session proof executed different controller bindings")
        admitted_launch = admission["launch_bindings"][arm]
        admitted = admission["arms"][arm]
        if (
            record["plan_id"] != admission["immutable_id"]
            or record["arm"] != arm
            or record["purpose"] != "engineering"
            or record["eligible_for_estimate"] is not False
            or configuration_digest(admitted, admitted_launch)
            != configuration_digest(candidate, launch)
            or "conformance_profile" not in admitted_launch
        ):
            raise ValueError("session proof does not bind this engineering launch")
        for name, digest in profile["controller_files"].items():
            if (
                admission["surface"]["artifact_hashes"].get(name) != digest
                or hashlib.sha256(artifacts["conformance/inputs/" + name]).hexdigest() != digest
            ):
                raise ValueError("session proof executed a different controller binding")
        return {"record": record, "admission": admission, "artifacts": artifacts}
    except (OSError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("cannot acquire a bound session proof") from exc
