"""Postflight integrity of typed staging and host-owned worker evidence."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from aisle.harness.treatment_confinement import MacOSPolicy, wrap_verified_command
from aisle.harness.typed_graph_hosts import _json_equal
from aisle.harness.typed_graph_stage import StageError, _verify_graph_stage
from aisle.harness.typed_node_host import load_host_config
from aisle.harness.typed_snapshot import _digest, _read
from aisle.harness.typed_worker_launch import _BOOTSTRAP, verify_typed_launch


def _json(root, name):
    return json.loads(_read(root, name))


def _worker(output, config, digest):
    host = _json(output, "host.json")
    terminal = _json(output, "result.json")
    if (
        host.get("host_config_sha256") != digest
        or not _json_equal(host.get("configuration"), config)
        or not _json_equal(host.get("result"), {**terminal, "host_config_sha256": digest})
        or host.get("ok") is not terminal.get("ok")
        or terminal.get("schema_version") != "aisle.typed-worker-launch-result.v1"
        or type(terminal.get("ok")) is not bool
    ):
        raise StageError("host result differs from its bound configuration or launcher record")
    if terminal.get("classification") == "infrastructure_exclusion":
        raise StageError("worker retained an infrastructure-invalid result")
    if terminal.get("classification") != "module_result" or terminal.get("stage") != "done":
        raise StageError("worker has no completed module result")
    launch = dict(config["launch"])
    launch["policy"] = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    compiled = verify_typed_launch(
        **{
            key: launch[key]
            for key in (
                "bundle",
                "bundle_manifest",
                "source_roots",
                "policy",
                "python",
                "python_sha256",
                "runtime_record",
                "environment",
                "environment_record",
            )
        }
    )
    command = [
        launch["python"],
        "-I",
        "-B",
        "-c",
        _BOOTSTRAP,
        str(Path(launch["bundle"]) / "src"),
        *sorted(launch["runtime_record"]["trees"]),
    ]
    wrapped = wrap_verified_command(
        command,
        compiled,
        output / "profile.sb",
        launch["attestation"],
    )
    for name, expected in (
        ("bundle.json", launch["bundle_manifest"]),
        ("runtime.json", launch["runtime_record"]),
        ("capability.json", launch["attestation"]),
    ):
        if not _json_equal(_json(output, name), expected):
            raise StageError("retained worker launch assets differ from configuration")
    worker = _json(output, "rpc/worker.json")
    invocation = _json(output, "launch.json")
    source_name = "src/" + config["module"].replace(".", "/") + ".py"
    source_hash = launch["bundle_manifest"]["files"][source_name]["sha256"]
    if (
        not _json_equal(terminal.get("worker"), worker)
        or worker.get("classification") != "module_result"
        or worker.get("ok") is not terminal["ok"]
        or type(worker.get("rc")) is not int
        or worker["rc"] not in (0, 1)
        or (worker["ok"] and worker["rc"] != 0)
        or worker.get("module") != config["module"]
        or worker.get("source_sha256") != source_hash
        or not _json_equal(worker.get("configuration"), config["configuration"])
    ):
        raise StageError("worker observations differ from the launch binding")
    expected_invocation = {
        "argv": command,
        "wrapped_argv": wrapped,
        "cwd": launch["bundle"],
        "module": config["module"],
        "configuration": config["configuration"],
        "source_sha256": source_hash,
        "outputs": sorted(config["outputs"]),
        "timeout_s": launch["timeout_s"],
        "max_calls": launch.get("max_calls", 100000),
        "python_sha256": launch["python_sha256"],
        "policy_id": compiled.policy_id,
        "profile_sha256": compiled.sha256,
        "environment_record": launch["environment_record"],
    }
    if not _json_equal(invocation, expected_invocation):
        raise StageError("retained launch command or grants differ from configuration")
    journal = [json.loads(line) for line in _read(output, "rpc/messages.jsonl").splitlines()]
    expected_frames = set()
    for sequence, message in enumerate(journal, 1):
        name = f"{sequence:08d}-{message['direction']}.frame"
        if (
            message["direction"] not in {"host", "worker"}
            or type(message["sequence"]) is not int
            or message["sequence"] != sequence
            or type(message["bytes"]) is not int
            or message["file"] != name
            or message["complete"] is not True
        ):
            raise StageError("worker protocol journal is incomplete or out of sequence")
        data = _read(output, "rpc/" + name)
        if len(data) != message["bytes"] or hashlib.sha256(data).hexdigest() != message["sha256"]:
            raise StageError("worker protocol frame differs from journal")
        expected_frames.add(name)
    if (
        type(worker.get("messages")) is not int
        or worker["messages"] != len(journal)
        or {p.name for p in (output / "rpc").glob("*.frame")} != expected_frames
    ):
        raise StageError("worker protocol inventory differs from terminal observations")
    return {
        "classification": "module_result",
        "module_ok": worker["ok"],
        "rc": worker["rc"],
        "host_config_sha256": digest,
    }


def audit_graph_stage(output, record):
    """Retain integrity findings and hashes after hosts stop, including failed attempts.

    This checks retained observations, not independently observed process-tree
    termination or external resource enforcement. Callers retain this report
    outside the stage and keep the underlying artifacts for review.
    """
    output = Path(output).absolute()
    report = {
        "schema_version": "aisle.typed-graph-audit.v1",
        "stage_root": str(output),
        "stage_id": record.get("immutable_id"),
        "ok": False,
        "workers": {},
        "files": {},
        "errors": [],
        "confirmatory_ready": False,
        "process_tree_verified": False,
    }
    try:
        configs, roots = {}, []
        for node_id, binding in record["hosts"].items():
            config = load_host_config(binding["config_path"], binding["config_sha256"])
            worker_root = Path(config["output"])
            if worker_root.parent != output / "workers" or worker_root in roots:
                raise StageError("worker evidence root differs from staging")
            configs[node_id] = config
            roots.append(worker_root)
        _verify_graph_stage(output, record, tuple(roots))
        for node_id, config in configs.items():
            try:
                report["workers"][node_id] = _worker(
                    Path(config["output"]),
                    config,
                    record["hosts"][node_id]["config_sha256"],
                )
            except Exception as exc:
                report["errors"].append({"node": node_id, "error": str(exc)})
    except Exception as exc:
        report["errors"].append({"node": None, "error": str(exc)})
    # Retain observed file identities even when a stage or worker check failed.
    try:
        for path in sorted(output.rglob("*")):
            mode = path.lstat().st_mode
            name = path.relative_to(output).as_posix()
            if stat.S_ISREG(mode):
                report["files"][name] = hashlib.sha256(_read(output, name)).hexdigest()
            elif not stat.S_ISDIR(mode):
                report["errors"].append(
                    {"node": None, "error": f"special or redirected entry: {name}"}
                )
    except Exception as exc:
        report["errors"].append({"node": None, "error": str(exc)})
    report["ok"] = not report["errors"] and set(report["workers"]) == set(record.get("hosts", {}))
    report["immutable_id"] = _digest(report)
    return report


def retain_graph_stage(source, destination, audit):
    """Copy raw postflight evidence into a fresh run-owned directory.

    Failed audits still have evidence worth retaining. Collection integrity is
    separate from audit success: callers require both and retain both reports.
    Redirected entries are reported without reading their targets.
    """
    source, destination = (Path(p).absolute() for p in (source, destination))
    if (
        destination.resolve() != destination
        or destination.is_relative_to(source.resolve())
        or source.resolve().is_relative_to(destination)
    ):
        raise ValueError("typed evidence destination is redirected or overlaps staging")
    destination.mkdir(parents=True, exist_ok=False)
    raw = destination / "raw"
    raw.mkdir()
    report = {
        "schema_version": "aisle.typed-graph-retention.v1",
        "stage_id": audit.get("stage_id"),
        "audit_id": audit.get("immutable_id"),
        "ok": False,
        "files": {},
        "errors": [],
    }
    expected = dict(audit)
    identity = expected.pop("immutable_id", None)
    try:
        if (
            _digest(expected) != identity
            or audit.get("schema_version") != "aisle.typed-graph-audit.v1"
            or audit.get("stage_root") != str(source)
        ):
            raise ValueError("postflight audit identity differs from staging")
    except Exception as exc:
        report["errors"].append(str(exc))
    try:
        if source.resolve() != source or not source.is_dir():
            raise ValueError("typed evidence source must be a canonical directory")
        paths = sorted(source.rglob("*"))
        for path in paths:
            name = path.relative_to(source).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode):
                    continue
                data = _read(source, name)
                target = raw / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(data)
                target.chmod(0o444)
                report["files"][name] = hashlib.sha256(data).hexdigest()
            except Exception as exc:
                report["errors"].append(f"{name}: {exc}")
        if sorted(source.rglob("*")) != paths:
            report["errors"].append("typed evidence inventory changed during collection")
        for name, digest in report["files"].items():
            try:
                if (
                    hashlib.sha256(_read(source, name)).hexdigest() != digest
                    or hashlib.sha256(_read(raw, name)).hexdigest() != digest
                ):
                    raise ValueError("typed evidence changed during collection")
            except Exception as exc:
                report["errors"].append(f"{name}: {exc}")
    except Exception as exc:
        report["errors"].append(str(exc))
    if report["files"] != audit.get("files"):
        report["errors"].append("collected file identities differ from postflight audit")
    report["ok"] = not report["errors"]
    report["immutable_id"] = _digest(report)
    with (destination / "collection.json").open("x") as stream:
        stream.write(json.dumps(report, indent=1))
    return report
