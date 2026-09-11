"""Versioned common session evidence assembled from controller observations.

Missing collections remain explicit. A successful engineering child process is
not sufficient evidence for a completed MON-12 session or a CSE-2 outcome.
"""

from __future__ import annotations

import copy
import math

SCHEMA = {
    "schema_version": "aisle.matched-common-evidence.v1",
    "required_collections": [
        "transcript",
        "tool_events",
        "runs",
        "evaluator",
        "guards",
    ],
}


def _artifact(artifacts: dict, name: str) -> dict:
    if name not in artifacts:
        return {"status": "not_collected", "path": None, "sha256": None}
    return {"status": "retained", "path": name, "sha256": artifacts[name]}


def _measurement(process: dict, key: str, *, integer: bool = False):
    value = process.get(key)
    permitted = (int,) if integer else (int, float)
    if (
        type(value) not in permitted
        or value < 0
        or (type(value) is float and not math.isfinite(value))
    ):
        return None
    return value


def _session_simulator_operations(runs):
    """Aggregate only re-audited run journals; missing producers never become zero work."""
    entries, unresolved = [], []
    for run in runs:
        collection = run["collection"]
        work = collection.get("simulator_work") if collection is not None else None
        if not isinstance(work, dict) or work.get("status") != "recomputed":
            unresolved.append(run["run_id"])
            continue
        entries.append(
            {
                "run_id": run["run_id"],
                "attempt_id": run["attempt_id"],
                "collection_path": run["collection_path"],
                "evidence": copy.deepcopy(work["summary"]),
            }
        )
    return {
        "status": "retained" if entries else "not_collected",
        "scope": "audited_run_journals",
        "entries": entries,
        "unresolved_runs": unresolved,
        "known_completed_env_steps": (
            sum(row["evidence"]["completed_env_steps"] for row in entries) if entries else None
        ),
        "known_completed_env_sim_ns": (
            sum(row["evidence"]["completed_env_sim_ns"] for row in entries) if entries else None
        ),
        "recorded_step_work_exact": bool(entries)
        and not unresolved
        and all(row["evidence"]["recorded_step_work_exact"] for row in entries),
        "producer_coverage_complete": False,
    }


def common_envelope(record: dict, manifest: dict | None, validation: dict | None) -> dict:
    """Summarize retained evidence without converting unknown quantities into zeros."""
    artifacts = record["artifacts"]
    process = record["process"] if isinstance(record["process"], dict) else {}
    tool_audit = record.get("tool_audit")
    verified_tools = isinstance(tool_audit, dict) and tool_audit.get("ok") is True
    frontend = record.get("frontend_tools")
    runs = copy.deepcopy(tool_audit.get("runs", [])) if verified_tools else []
    collections = {}
    for kind in ("evaluator", "guards"):
        entries = []
        for run in runs:
            collection = run["collection"]
            if collection is not None and collection[kind].get("status") == "retained":
                entries.append(
                    {
                        "run_id": run["run_id"],
                        "attempt_id": run["attempt_id"],
                        "collection_path": run["collection_path"],
                        "evidence": copy.deepcopy(collection[kind]),
                    }
                )
        collections[kind] = {
            "status": "retained" if entries else "not_collected",
            "entries": entries,
        }
    observed = {
        "tokens": _measurement(process, "tokens", integer=True),
        "tokens_generated": _measurement(process, "tokens_generated", integer=True),
        "wall_s": _measurement(process, "wall_s"),
        "tool_calls": None,
        "frontend_tool_calls": (
            frontend["observed_calls"]
            if isinstance(frontend, dict) and frontend.get("ok") is True
            else None
        ),
        "controller_tool_requests": tool_audit["attempted_tools"] if verified_tools else None,
        "controller_reserved_runs": tool_audit["reserved_runs"] if verified_tools else None,
        "controller_reserved_episodes": tool_audit["reserved_episodes"] if verified_tools else None,
        "controller_tool_processes": tool_audit["executed_tools"] if verified_tools else None,
        "controller_tool_wall_s": tool_audit["wall_s"] if verified_tools else None,
        "simulator_work": None,
    }
    result = {
        "schema_version": SCHEMA["schema_version"],
        "complete": False,
        "assignment": {
            "session_id": record["session_id"],
            "arm": record["arm"],
            "plan_id": record["plan_id"],
            "treatment_id": manifest["immutable_id"] if manifest else None,
            "block_assignment": copy.deepcopy(manifest["assignment"]) if manifest else None,
            "admission_verified": manifest is not None,
        },
        "lifecycle": copy.deepcopy(record["lifecycle"]),
        "budgets": {
            "declared": copy.deepcopy(manifest["budget"]) if manifest else None,
            "observed": observed,
        },
        "transcript": _artifact(artifacts, "session.jsonl"),
        "tool_events": _artifact(artifacts, "tool-events.jsonl"),
        "runs": {"status": "retained" if runs else "not_collected", "entries": runs},
        "evaluator": collections["evaluator"],
        "guards": collections["guards"],
        "simulator_operations": _session_simulator_operations(runs),
        "snapshots": copy.deepcopy(record["snapshots"]),
        "attempts": copy.deepcopy(record["events"]),
        "validation": copy.deepcopy(validation),
        "audits": {
            "preflight": _artifact(artifacts, "admission.json"),
            "postflight": copy.deepcopy(record["postflight"]),
            "tools": copy.deepcopy(tool_audit),
            "frontend_tools": copy.deepcopy(frontend),
            "private_state": copy.deepcopy(record.get("private_state")),
            "conformance_profile": copy.deepcopy(record.get("conformance_profile")),
        },
        "exclusions": [record["error"]] if record["error"] else [],
        "content_hashes": copy.deepcopy(artifacts),
    }
    result["missing_collections"] = [
        name for name in SCHEMA["required_collections"] if result[name]["status"] != "retained"
    ]
    # Completion additionally needs validated collection contents and controller
    # tool/run accounting. File presence alone never grants completeness.
    return result


def retain_run(source, destination, *, run_id: str) -> dict:
    """Retain a controller-selected engineering run; never infer study eligibility.

    The collector consumes the actual rollout file layout, retains raw bytes,
    and separates successful collection from episode success. It neither runs
    the evaluator nor changes the source run's purpose or outcomes.
    """
    import hashlib
    import json
    from pathlib import Path

    source = Path(source).absolute()
    destination = Path(destination).resolve()
    if destination.is_relative_to(source.resolve()) or source.resolve().is_relative_to(destination):
        raise ValueError("run evidence destination overlaps its source")
    destination.mkdir(parents=True, exist_ok=False)
    raw = destination / "raw"
    raw.mkdir()
    report = {
        "schema_version": "aisle.matched-run-evidence.v1",
        "run_id": run_id,
        "ok": False,
        "eligible_for_estimate": False,
        "files": {},
        "manifest": None,
        "episodes": None,
        "evaluator": {"status": "not_collected"},
        "guards": {"status": "not_collected"},
        "simulator": _episode_simulator_time(None),
        "error": None,
    }

    def strict_json(data: str):
        def invalid_number(value):
            raise ValueError(f"non-finite run evidence number: {value}")

        return json.loads(data, parse_constant=invalid_number)

    try:
        if source.resolve() != source or not source.is_dir():
            raise ValueError("run source must be a canonical directory")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run identity is unresolved")
        paths = sorted(source.rglob("*"))
        for path in paths:
            name = path.relative_to(source).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError(f"run artifact is a symlink or escapes source: {name}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"run artifact is not a regular file: {name}")
            target = raw / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with path.open("rb") as incoming, target.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
                    digest.update(chunk)
            report["files"][name] = digest.hexdigest()
        if sorted(source.rglob("*")) != paths:
            raise ValueError("run artifacts changed during collection")
        for name, expected in report["files"].items():
            path = source / name
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("run artifact redirected during collection")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError(f"run artifact changed during collection: {name}")
        manifest = strict_json((raw / "manifest.json").read_text())
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise ValueError("run manifest identity differs from requested run")
        report["manifest"] = manifest
        episodes = [
            strict_json(line)
            for line in (raw / "episodes.jsonl").read_text().splitlines()
            if line.strip()
        ]
        if any(not isinstance(episode, dict) for episode in episodes):
            raise ValueError("episode evidence must contain objects")
        report["episodes"] = episodes
        report["simulator"] = _episode_simulator_time(episodes)
        report["evaluator"] = _retained_evaluator_evidence(manifest, episodes)
        report["guards"] = _retained_guard_evidence(raw)
        report["ok"] = True
    except (OSError, ValueError, TypeError) as exc:
        report["error"] = str(exc)
    report["simulator_work"] = _retained_simulator_work(raw, report["manifest"])
    with (destination / "run-evidence.json").open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return report


def _retained_simulator_work(raw, manifest):
    """Recompute operation evidence from retained bytes, never from the stored summary."""
    import hashlib

    from aisle.harness.simulator_work import summarize_launches

    directory = raw / "simulator-work"
    result = {"status": "not_collected", "summary": None, "error": None}
    if not directory.exists() and not (raw / "simulator-work-summary.json").exists():
        return result
    result["status"] = "unresolved"
    try:
        if not isinstance(manifest, dict):
            raise ValueError("simulator work lacks a run manifest")
        hashes = manifest.get("exec_graph_hashes")
        if not isinstance(hashes, list) or not hashes:
            raise ValueError("simulator work lacks ordered execution graph hashes")
        for index, expected in enumerate(hashes):
            graph = raw / (f"graph-r{index}.yaml" if index else "graph.yaml")
            if graph.resolve() != graph or not graph.is_file():
                raise ValueError("executed simulator graph is missing or redirected")
            if hashlib.sha256(graph.read_bytes()).hexdigest() != expected:
                raise ValueError("executed simulator graph digest differs")
        result["summary"] = summarize_launches(
            directory, run_id=manifest["run_id"], expected_graph_hashes=hashes
        )
        result["status"] = "recomputed"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result["error"] = str(exc)
    return result


def _episode_simulator_time(episodes) -> dict:
    report = {
        "status": "not_collected",
        "unit": "episode_sim_seconds",
        "value": None,
        "observed_episodes": len(episodes) if isinstance(episodes, list) else None,
        "includes_reset_work": False,
    }
    if not isinstance(episodes, list) or not episodes:
        return report
    durations = [episode.get("t_end") for episode in episodes]
    try:
        if any(
            type(value) not in (int, float) or value < 0 or not math.isfinite(value)
            for value in durations
        ):
            return report
        total = sum(durations)
        if not math.isfinite(total):
            return report
    except OverflowError:
        return report
    report.update(status="retained", value=total)
    return report


def _retained_evaluator_evidence(manifest: dict, episodes: list[dict]) -> dict:
    verifier = manifest.get("verifier")
    seeds = manifest.get("seeds")
    if not isinstance(verifier, str) or not verifier.strip():
        raise ValueError("run verifier identity is missing")
    if not isinstance(seeds, list) or not seeds or any(type(seed) is not int for seed in seeds):
        raise ValueError("run planned seeds are unresolved")
    observed = [episode.get("seed") for episode in episodes]
    if any(type(seed) is not int or seed not in seeds for seed in observed):
        raise ValueError("episode seed is missing or outside the declared run")
    return {
        "status": "retained",
        "verifier": verifier,
        "planned_seeds": seeds,
        "observed_seeds": observed,
        "complete": observed == seeds,
        "episodes_path": "raw/episodes.jsonl",
    }


def _retained_guard_evidence(raw) -> dict:
    import pyarrow as pa

    from aisle.harness.guard_divergence import summary_for_run

    traces = raw / "traces"
    paths = sorted(
        {
            path
            for topic in ("joint_cmd_safe", "violation")
            for pattern in (f"*__{topic}.arrow", f"{topic}.arrow")
            for path in traces.glob(pattern)
        }
    )
    if not paths:
        return {"status": "not_collected"}
    for path in paths:
        try:
            with pa.ipc.open_stream(path) as stream:
                stream.read_all()
        except (pa.ArrowInvalid, pa.ArrowIOError, OSError) as exc:
            raise ValueError(f"invalid Arrow guard stream {path.name}: {exc}") from exc
    summary = summary_for_run(raw)
    if summary.get("error"):
        raise ValueError(f"guard evidence is unresolved: {summary['error']}")
    return {
        "status": "retained",
        "summary": summary,
        "traces": [str(path.relative_to(raw)) for path in paths],
    }


def audit_tool_journal(
    output,
    *,
    session_id: str,
    plan_id: str,
    arm: str,
    development=None,
    request_authority=None,
    require_frontend_source=False,
    frontend_dispatch_ceiling=None,
) -> dict:
    """Bind journal entries to session identity, immutable attempts and retained bytes."""
    import hashlib
    import json
    from pathlib import Path

    output = Path(output).resolve()
    report = {
        "schema_version": "aisle.matched-tool-audit.v1",
        "ok": False,
        "session_id": session_id,
        "plan_id": plan_id,
        "arm": arm,
        "attempted_tools": 0,
        "executed_tools": 0,
        "reserved_runs": 0,
        "reserved_episodes": 0,
        "wall_s": 0.0,
        "service_verified": False,
        "frontend_authorization_verified": False,
        "frontend_source_verified": False,
        "frontend_reservation_verified": False,
        "files": {},
        "attempt_ids": [],
        "runs": [],
        "exclusions": [],
        "error": None,
    }

    def read(name):
        path = output / name
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"tool evidence path is missing or redirected: {name}")
        data = path.read_bytes()
        report["files"][name] = hashlib.sha256(data).hexdigest()
        return data

    def decode(data):
        def invalid(value):
            raise ValueError(f"non-finite tool evidence value: {value}")

        return json.loads(data, parse_constant=invalid)

    def lines(name):
        data = read(name)
        if data and not data.endswith(b"\n"):
            raise ValueError(f"tool journal has an unfinished line: {name}")
        return [decode(line) for line in data.splitlines()]

    try:
        if type(require_frontend_source) is not bool:
            raise ValueError("invalid frontend source requirement")
        if frontend_dispatch_ceiling is not None and (
            type(frontend_dispatch_ceiling) is not int or frontend_dispatch_ceiling <= 0
        ):
            raise ValueError("invalid admitted frontend dispatch ceiling")
        events = lines("tool-events.jsonl")
        if len(events) % 2:
            raise ValueError("tool journal lacks a finished attempt")
        attempts = {}
        operations = {}
        for offset in range(0, len(events), 2):
            start, finish = events[offset : offset + 2]
            number = offset // 2 + 1
            if not isinstance(start, dict) or start.get("operation") not in ("check", "run"):
                raise ValueError("tool operation is unresolved")
            operation = start["operation"]
            expected = {
                "session_id": session_id,
                "plan_id": plan_id,
                "arm": arm,
                "attempt": number,
                "operation": operation,
            }
            if not isinstance(start, dict) or start != {"event": "started", **expected}:
                raise ValueError("tool start identity or sequence differs")
            if (
                not isinstance(finish, dict)
                or set(finish) != {"event", "record"}
                or finish["event"] != "finished"
            ):
                raise ValueError("tool finish event is unresolved")
            record = finish["record"]
            if not isinstance(record, dict) or any(
                record.get(key) != value for key, value in expected.items()
            ):
                raise ValueError("tool finish belongs to a different session or attempt")
            if (
                record.get("schema_version") != "aisle.matched-tool-attempt.v1"
                or record.get("eligible_for_estimate") is not False
            ):
                raise ValueError("tool attempt schema or engineering purpose is invalid")
            payload = dict(record)
            identity = payload.pop("immutable_id")
            computed = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode()
                ).hexdigest()
            )
            if identity != computed:
                raise ValueError("tool attempt immutable identity drift")
            from datetime import datetime

            lifecycle = record.get("lifecycle")
            if not isinstance(lifecycle, dict) or set(lifecycle) != {"started_at", "finished_at"}:
                raise ValueError("tool lifecycle timestamps are missing")
            try:
                started_at = datetime.fromisoformat(lifecycle["started_at"])
                finished_at = datetime.fromisoformat(lifecycle["finished_at"])
                if (
                    started_at.utcoffset() is None
                    or finished_at.utcoffset() is None
                    or finished_at < started_at
                ):
                    raise ValueError("unordered or timezone-free instants")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid tool lifecycle: {exc}") from exc
            if operation == "run":
                if development is None:
                    raise ValueError("development protocol is required to audit a run")
                reservation = record.get("reservation")
                if (
                    not isinstance(reservation, dict)
                    or set(reservation) != {"runs", "episodes"}
                    or any(type(value) is not int for value in reservation.values())
                    or reservation
                    not in (
                        {"runs": 0, "episodes": 0},
                        {"runs": 1, "episodes": len(development["seeds"])},
                    )
                    or (record.get("process") is not None and reservation["runs"] != 1)
                ):
                    raise ValueError("run reservation differs from admitted protocol")
                report["reserved_runs"] += reservation["runs"]
                report["reserved_episodes"] += reservation["episodes"]
                if (
                    report["reserved_runs"] > development["run_ceiling"]
                    or report["reserved_episodes"] > development["episode_ceiling"]
                ):
                    raise ValueError("run reservations exceed admitted budget")
                development_id = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            development, sort_keys=True, separators=(",", ":"), allow_nan=False
                        ).encode()
                    ).hexdigest()
                )
                if (
                    record.get("process") is not None or record.get("development_id") is not None
                ) and record["development_id"] != development_id:
                    raise ValueError("development protocol identity drift")
                expected_run_id = (
                    "matched-"
                    + hashlib.sha256(
                        json.dumps(
                            {"session": session_id, "plan": plan_id, "arm": arm, "attempt": number},
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode()
                    ).hexdigest()[:24]
                )
                if (
                    record.get("process") is not None or record.get("run_id") is not None
                ) and record.get("run_id") != expected_run_id:
                    raise ValueError("run identity differs from session and attempt")
            directory = f"tool-{number:06d}"
            if decode(read(f"{directory}/attempt.json")) != record:
                raise ValueError("tool journal and retained attempt disagree")
            collection = record.get("run_evidence")
            run_directory = output / directory / "run"
            collection_files = set()
            incomplete_collection = False
            if record.get("collection_process") is not None:
                if (
                    record["collection_process"] != "run-collector/process.json"
                    or operation != "run"
                ):
                    raise ValueError("invalid collection process reference")
                receipt = decode(read(f"{directory}/run-collector/process.json"))
                if (
                    type(receipt) is not dict
                    or receipt.get("schema_version") != "aisle.matched-collection-process.v1"
                    or type(receipt.get("ok")) is not bool
                    or type(receipt.get("wall_s")) not in (int, float)
                    or not math.isfinite(receipt["wall_s"])
                    or receipt["wall_s"] < 0
                    or type(receipt.get("timeout_s")) not in (int, float)
                    or not math.isfinite(receipt["timeout_s"])
                    or receipt["timeout_s"] <= 0
                    or (
                        receipt.get("error") is not None
                        and (type(receipt["error"]) is not str or not receipt["error"])
                    )
                ):
                    raise ValueError("invalid collection process receipt")
                collection_files = {
                    "run-collector/" + name
                    for name in ("process.json", "invocation.json", "stdout.log", "stderr.log")
                    if "run-collector/" + name in record.get("artifacts", {})
                }
                required_collection = {"run-collector/process.json"}
                process = receipt.get("process")
                if process is not None:
                    if (
                        type(process) is not dict
                        or type(process.get("rc")) is not int
                        or type(process.get("timed_out")) is not bool
                    ):
                        raise ValueError("collection child lacks a terminal process record")
                    required_collection.update(
                        "run-collector/" + name
                        for name in ("invocation.json", "stdout.log", "stderr.log")
                    )
                    invocation = decode(read(f"{directory}/run-collector/invocation.json"))
                    from aisle.harness.matched_collection import _BOOTSTRAP

                    command = invocation.get("argv") if type(invocation) is dict else None
                    if (
                        type(command) is not list
                        or len(command) != 9
                        or command[1:5] != ["-I", "-B", "-c", _BOOTSTRAP]
                        or command[-1] != record["run_id"]
                    ):
                        raise ValueError("collection invocation differs from the run")
                    if process["timed_out"] or receipt.get("cleanup") is not None:
                        cleanup = receipt.get("cleanup")
                        if (
                            type(cleanup) is not dict
                            or cleanup.get("ok") is not True
                            or cleanup.get("remaining") != []
                            or cleanup.get("errors") != []
                        ):
                            raise ValueError("collection process cleanup is unresolved")
                if not required_collection.issubset(collection_files):
                    raise ValueError("collection process artifacts are missing")
                if collection is None:
                    incomplete_collection = (
                        receipt["ok"] is False
                        and bool(receipt.get("error"))
                        and record["classification"] == "infrastructure_exclusion"
                        and record["ok"] is False
                    )
                    if not incomplete_collection:
                        raise ValueError("collection receipt cannot explain missing evidence")
                elif (
                    type(collection) is not dict
                    or receipt["ok"] is not collection.get("ok")
                    or receipt.get("error") is not None
                    or receipt["wall_s"] >= receipt["timeout_s"]
                    or process is None
                    or process["timed_out"]
                    or process["rc"] != int(not collection["ok"])
                ):
                    raise ValueError("collection receipt differs from retained verdict")
            if collection is not None:
                if (
                    operation != "run"
                    or not isinstance(collection, dict)
                    or collection.get("schema_version") != "aisle.matched-run-evidence.v1"
                    or collection.get("eligible_for_estimate") is not False
                    or collection.get("run_id") != record.get("run_id")
                    or not isinstance(collection.get("files"), dict)
                    or type(collection.get("ok")) is not bool
                    or any(
                        not isinstance(collection.get(kind), dict)
                        or collection[kind].get("status") not in {"retained", "not_collected"}
                        for kind in ("evaluator", "guards")
                    )
                ):
                    raise ValueError("retained run identity or schema is invalid")
                if decode(read(f"{directory}/run/run-evidence.json")) != collection:
                    raise ValueError("retained run report differs from tool record")
                indexed_paths = {"run-evidence.json"}
                for name, expected_hash in collection["files"].items():
                    if (
                        not isinstance(name, str)
                        or not name
                        or Path(name).is_absolute()
                        or ".." in Path(name).parts
                        or Path(name).as_posix() != name
                    ):
                        raise ValueError("retained run artifact path is invalid")
                    relative = f"raw/{name}"
                    indexed_paths.add(relative)
                    key = f"{directory}/run/{relative}"
                    read(key)
                    if report["files"][key] != expected_hash:
                        raise ValueError("retained run artifact hash drift")
                actual_paths = set()
                for path in run_directory.rglob("*"):
                    if path.is_symlink() or path.resolve() != path:
                        raise ValueError("retained run artifact is redirected")
                    if not path.is_dir():
                        actual_paths.add(path.relative_to(run_directory).as_posix())
                if actual_paths != indexed_paths:
                    raise ValueError("retained run files differ from artifact index")
                # Reconstruct reported values from the verified raw files. A
                # self-consistent rehash of a summary cannot rewrite outcomes.
                raw_manifest = collection.get("manifest")
                raw_episodes = collection.get("episodes")
                if collection["ok"] or raw_manifest is not None:
                    parsed = decode(read(f"{directory}/run/raw/manifest.json"))
                    if (
                        parsed != raw_manifest
                        or not isinstance(parsed, dict)
                        or parsed.get("run_id") != record.get("run_id")
                    ):
                        raise ValueError("run manifest summary differs from raw evidence")
                if collection["ok"] or raw_episodes is not None:
                    parsed = [
                        decode(line)
                        for line in read(f"{directory}/run/raw/episodes.jsonl").splitlines()
                        if line.strip()
                    ]
                    if parsed != raw_episodes or any(not isinstance(item, dict) for item in parsed):
                        raise ValueError("run episode summary differs from raw evidence")
                if collection["ok"] or collection["evaluator"]["status"] == "retained":
                    if not isinstance(raw_manifest, dict) or not isinstance(raw_episodes, list):
                        raise ValueError("evaluator summary lacks raw run evidence")
                    if collection["evaluator"] != _retained_evaluator_evidence(
                        raw_manifest, raw_episodes
                    ):
                        raise ValueError("evaluator summary differs from raw evidence")
                if collection.get("simulator_work") != _retained_simulator_work(
                    run_directory / "raw", raw_manifest
                ):
                    raise ValueError("simulator work summary differs from raw launch evidence")
                if collection.get("simulator") != _episode_simulator_time(raw_episodes):
                    raise ValueError("simulator summary differs from raw episode evidence")
                if collection["ok"] or collection["guards"]["status"] == "retained":
                    expected_guards = _retained_guard_evidence(run_directory / "raw")
                    if expected_guards["status"] == "retained":
                        summary = collection["guards"].get("summary")
                        if not isinstance(summary, dict) or not isinstance(
                            summary.get("run_dir"), str
                        ):
                            raise ValueError("guard summary lacks its diagnostic source directory")
                        # The absolute collection location is diagnostic, not a
                        # measured guard value. Replay may relocate authenticated
                        # traces; compare every statistic and trace name unchanged.
                        expected_guards["summary"]["run_dir"] = summary["run_dir"]
                    if collection["guards"] != expected_guards:
                        raise ValueError("guard summary differs from raw evidence")
            elif (
                run_directory.exists() or run_directory.is_symlink()
            ) and not incomplete_collection:
                raise ValueError("retained run directory lacks a collection record")
            if not isinstance(record.get("artifacts"), dict):
                raise ValueError("tool artifact index is invalid")
            snapshot_files = set()
            archive = record.get("snapshot_archive")
            if archive is not None and "error" not in archive:
                snapshot = decode(read(f"{directory}/source-snapshot/snapshot.json"))
                validated = decode(read(f"{directory}/validation/snapshot.json"))
                if snapshot != validated or archive["snapshot_id"] != snapshot["immutable_id"]:
                    raise ValueError("archived snapshot differs from validation inputs")
                expected_files = {name: item["sha256"] for name, item in snapshot["files"].items()}
                expected_files["snapshot.json"] = report["files"][
                    f"{directory}/source-snapshot/snapshot.json"
                ]
                if archive["files"] != expected_files:
                    raise ValueError("snapshot archive inventory differs")
                for name, digest in expected_files.items():
                    if Path(name).is_absolute() or ".." in Path(name).parts:
                        raise ValueError("snapshot archive path escapes evidence")
                    name = "source-snapshot/" + name
                    if record["artifacts"].get(name) != digest:
                        raise ValueError("snapshot archive artifact index differs")
                    snapshot_files.add(name)
            required_process_files = {"stdout.json", "stderr.log", "profile.sb", "invocation.json"}
            controller_files = set()
            provider_files = set()
            declaration_files = set()
            preparation = record.get("worker_preparation")
            if preparation is not None:
                if (
                    operation != "run"
                    or set(preparation) != {"index", "sha256"}
                    or type(preparation["index"]) is not int
                    or preparation["index"] != report["reserved_runs"] - 1
                    or record["reservation"]["runs"] != 1
                ):
                    raise ValueError("worker preparation sequence differs from reserved run")
                data = read(f"{directory}/worker-declaration.json")
                if (
                    hashlib.sha256(data).hexdigest() != preparation["sha256"]
                    or record["artifacts"].get("worker-declaration.json") != preparation["sha256"]
                ):
                    raise ValueError("worker preparation declaration hash differs")
                decode(data)
                declaration_files.add("worker-declaration.json")
            preparation_files = set()
            if arm == "monolithic":
                preparation_files = {
                    "monolithic-input/module.py",
                    "monolithic-input/worker-config.json",
                }
                if "monolithic-input/worker-config.json" in record["artifacts"]:
                    prepared_worker = decode(
                        read(f"{directory}/monolithic-input/worker-config.json")
                    )
                    module_bytes = read(f"{directory}/monolithic-input/module.py")
                    if hashlib.sha256(module_bytes).hexdigest() != prepared_worker["module_sha256"]:
                        raise ValueError("prepared module differs from worker configuration")
            worker_files = set()
            worker_evidence = (record.get("result") or {}).get("worker_evidence")
            worker_directory = output / directory / "run-controller/monolithic-worker"
            if worker_evidence is not None:
                if arm != "monolithic" or operation != "run" or not record.get("run_controller"):
                    raise ValueError("worker evidence belongs to another arm or operation")
                prefix = "run-controller/monolithic-worker/"
                retained = decode(read(f"{directory}/{prefix}collection.json"))
                if (
                    retained != worker_evidence
                    or retained.get("schema_version") != "aisle.monolithic-worker-retention.v1"
                    or retained.get("eligible_for_estimate") is not False
                    or type(retained.get("worker_evidence_present")) is not bool
                    or not isinstance(retained.get("errors"), list)
                    or retained.get("ok") is not (not retained["errors"])
                    or (not retained["worker_evidence_present"] and retained["files"])
                ):
                    raise ValueError("worker collection differs from run result")
                worker_files = {
                    prefix + name for name in ("collection.json", "worker-config.json", "module.py")
                }
                for name, digest in {
                    "worker-config.json": retained["config_sha256"],
                    "module.py": retained["module_sha256"],
                    **{"raw/" + name: digest for name, digest in retained["files"].items()},
                }.items():
                    if Path(name).is_absolute() or ".." in Path(name).parts:
                        raise ValueError("worker evidence path escapes collection")
                    key = f"{directory}/{prefix}{name}"
                    read(key)
                    if report["files"][key] != digest:
                        raise ValueError("worker evidence hash differs")
                    worker_files.add(prefix + name)
                worker_config = decode(read(f"{directory}/{prefix}worker-config.json"))
                if (
                    worker_config["output_root"] != retained["source_root"]
                    or worker_config["module_sha256"] != retained["module_sha256"]
                ):
                    raise ValueError("worker evidence differs from worker configuration")
                if not worker_files.issubset(record["artifacts"]):
                    raise ValueError("worker evidence artifact index is incomplete")
                actual_files = set()
                for path in worker_directory.rglob("*"):
                    if path.is_symlink() or path.resolve() != path:
                        raise ValueError("worker evidence inventory is redirected")
                    if path.is_dir():
                        continue
                    if not path.is_file():
                        raise ValueError("worker evidence inventory contains a special entry")
                    actual_files.add(prefix + path.relative_to(worker_directory).as_posix())
                if actual_files != worker_files:
                    raise ValueError("worker evidence inventory differs from collection")
            elif worker_directory.exists() or worker_directory.is_symlink():
                raise ValueError("worker evidence directory lacks its collection result")
            controller = record.get("run_controller")
            if controller is not None:
                if operation != "run" or set(controller) != {"config_path", "config_sha256"}:
                    raise ValueError("run controller declaration is invalid")
                config_bytes = read(f"{directory}/run-config.json")
                if hashlib.sha256(config_bytes).hexdigest() != controller["config_sha256"]:
                    raise ValueError("retained run configuration hash differs")
                config = decode(config_bytes)
                if set(config["launch"]) == {"provider"}:
                    # Dynamic provisioning retains raw capability observations and
                    # generated inputs. Their closed inventory establishes retention,
                    # not the semantic validity of every capability observation.
                    subtrees = (
                        ("monolithic-provider", "monolithic-prepared")
                        if arm == "monolithic"
                        else ("typed-provider",)
                    )
                    for subtree in subtrees:
                        prefix = f"run-controller/{subtree}/"
                        root = output / directory / "run-controller" / subtree
                        indexed = {name for name in record["artifacts"] if name.startswith(prefix)}
                        actual = set()
                        if root.exists() or root.is_symlink():
                            if root.is_symlink() or not root.is_dir() or root.resolve() != root:
                                raise ValueError("dynamic provider evidence is redirected")
                            for path in root.rglob("*"):
                                if path.is_symlink() or path.resolve() != path:
                                    raise ValueError("dynamic provider evidence is redirected")
                                if path.is_dir():
                                    continue
                                if not path.is_file():
                                    raise ValueError("dynamic provider evidence is not regular")
                                actual.add(prefix + path.relative_to(root).as_posix())
                        if actual != indexed:
                            raise ValueError("dynamic provider evidence inventory differs")
                        provider_files.update(indexed)
                    if arm == "typed":
                        from aisle.harness.typed_snapshot import _digest

                        snapshot = config["launch"]["provider"]["snapshot_record"]
                        for name in sorted(provider_files):
                            parts = Path(name).parts
                            if len(parts) != 4 or parts[-1] != "result.json":
                                continue
                            receipt = decode(read(f"{directory}/{name}"))
                            index = receipt.get("launch")
                            if (
                                receipt.get("schema_version") != "aisle.typed-stage-provisioning.v1"
                                or type(index) is not int
                                or index < 0
                                or parts[2] != f"launch-{index}"
                                or receipt.get("snapshot_id") != snapshot["immutable_id"]
                                or type(receipt.get("ok")) is not bool
                            ):
                                raise ValueError("typed provider receipt differs from sealed run")
                            if not receipt["ok"]:
                                continue
                            stage_prefix = str(Path(name).parent / "stages/stage-0")
                            stage_name = stage_prefix + "/stage.json"
                            if stage_name not in provider_files:
                                raise ValueError("typed provider stage receipt is missing")
                            stage = decode(read(f"{directory}/{stage_name}"))
                            stage_id = stage.pop("immutable_id")
                            expected_root = str(
                                Path(controller["config_path"]).parent / stage_prefix
                            )
                            if (
                                stage_id != _digest(stage)
                                or receipt.get("stage_id") != stage_id
                                or receipt.get("stage_root") != expected_root
                                or stage.get("stage_root") != expected_root
                                or stage.get("snapshot_record") != snapshot
                                or stage.get("controller_root") != config["controller_root"]
                            ):
                                raise ValueError("typed provider stage differs from sealed run")
                            for relative, digest in stage["files"].items():
                                if Path(relative).is_absolute() or ".." in Path(relative).parts:
                                    raise ValueError("typed provider stage path escapes evidence")
                                if record["artifacts"].get(stage_prefix + "/" + relative) != digest:
                                    raise ValueError("typed provider stage file binding differs")
                if arm == "monolithic" and set(config["launch"]) == {"provider"}:
                    module_name = "monolithic-input/module.py"
                    if preparation is not None and module_name not in record["artifacts"]:
                        raise ValueError("dynamic monolithic preparation lacks captured source")
                    if module_name in record["artifacts"] and (
                        hashlib.sha256(read(f"{directory}/{module_name}")).hexdigest()
                        != config["launch"]["provider"]["module_sha256"]
                    ):
                        raise ValueError("captured module differs from sealed provider request")
                prepared_hash = record["artifacts"].get("monolithic-input/worker-config.json")
                if (
                    prepared_hash is not None
                    and config["launch"].get("worker_config_sha256") != prepared_hash
                ):
                    raise ValueError("prepared worker configuration differs from run selection")
                if worker_evidence is not None:
                    if set(config["launch"]) == {"provider"}:
                        from aisle.harness.matched_dynamic_run import (
                            verify_monolithic_provider_binding,
                        )

                        provider_name = "run-controller/monolithic-provider/binding.json"
                        if provider_name not in record["artifacts"]:
                            raise ValueError("monolithic provider binding is missing from evidence")
                        verify_monolithic_provider_binding(
                            config,
                            controller["config_sha256"],
                            decode(read(f"{directory}/{provider_name}")),
                            worker_config,
                            worker_evidence["config_sha256"],
                        )
                    elif (
                        config["launch"]["worker_config_sha256"] != worker_evidence["config_sha256"]
                    ):
                        raise ValueError("worker evidence differs from run configuration")
                if any(
                    config.get(key) != value
                    for key, value in {
                        "schema_version": "aisle.matched-run-config.v1",
                        "purpose": "expert_parity",
                        "session_id": session_id,
                        "plan_id": plan_id,
                        "arm": arm,
                        "run_id": record["run_id"],
                        "development": development,
                    }.items()
                ):
                    raise ValueError("retained run configuration identity differs")
                controller_files = {
                    "run-config.json",
                    "run-controller/process.json",
                    "run-controller/stdout.json",
                    "run-controller/stderr.log",
                    "run-controller/invocation.json",
                }
                required_process_files = {
                    "stdout.json",
                    "stderr.log",
                    "invocation.json",
                    *controller_files,
                }
                terminal_path = output / directory / "run-controller/process.json"
                if terminal_path.exists() or record["process"] is not None:
                    terminal = decode(read(f"{directory}/run-controller/process.json"))
                    if (
                        terminal.get("schema_version") != "aisle.matched-run-process.v1"
                        or terminal.get("process") != record["process"]
                        or terminal.get("result") != record["result"]
                    ):
                        raise ValueError("run controller terminal record differs")
                if record["process"] is not None:
                    for name in ("stdout.json", "stderr.log", "invocation.json"):
                        if read(f"{directory}/{name}") != read(
                            f"{directory}/run-controller/{name}"
                        ):
                            raise ValueError("run controller journal copy differs")
                    invocation = decode(read(f"{directory}/run-controller/invocation.json"))
                    argv = invocation.get("argv", [])
                    if (
                        argv[-4:]
                        != [
                            "--config",
                            controller["config_path"],
                            "--config-sha256",
                            controller["config_sha256"],
                        ]
                        or invocation.get("cwd") != config["controller_root"]
                        or invocation.get("runtime_id") != config["runtime_record"]["immutable_id"]
                    ):
                        raise ValueError("run controller invocation differs from configuration")
            if record.get("process") is not None and not required_process_files.issubset(
                record["artifacts"]
            ):
                raise ValueError("required tool process artifacts are missing")
            for name, expected_hash in record["artifacts"].items():
                validation_files = {
                    "validation/" + item
                    for item in (
                        "stdout.json",
                        "stderr.log",
                        "profile.sb",
                        "launch.json",
                        "bundle.json",
                        "snapshot.json",
                        "capability.json",
                        "runtime.json",
                        "result.json",
                    )
                }
                if name not in (
                    required_process_files
                    | validation_files
                    | snapshot_files
                    | controller_files
                    | worker_files
                    | preparation_files
                    | declaration_files
                    | provider_files
                    | collection_files
                    | {"profile.sb"}
                ):
                    raise ValueError("tool artifact name is undeclared")
                read(f"{directory}/{name}")
                if report["files"][f"{directory}/{name}"] != expected_hash:
                    raise ValueError("tool artifact hash drift")
            elapsed = record["wall_s"]
            if type(elapsed) not in (int, float) or elapsed < 0:
                raise ValueError("tool elapsed time is invalid")
            if record["classification"] not in {"tool_result", "infrastructure_exclusion"}:
                raise ValueError("tool classification is invalid")
            if record.get("result") is not None:
                if decode(read(f"{directory}/stdout.json")) != record["result"]:
                    raise ValueError("tool verdict differs from retained stdout")
            if record["classification"] == "tool_result":
                result, process = record.get("result"), record.get("process")
                if (
                    not isinstance(result, dict)
                    or type(result.get("ok")) is not bool
                    or not isinstance(process, dict)
                    or type(process.get("rc")) is not int
                    or process["rc"] != (0 if result["ok"] else 1)
                    or record.get("ok") is not result["ok"]
                ):
                    raise ValueError("tool verdict and observed exit status disagree")
            if record["process"] is not None:
                if (
                    not isinstance(record["process"], dict)
                    or type(record["process"].get("rc")) is not int
                ):
                    raise ValueError("tool process record is invalid")
                report["executed_tools"] += 1
            if record["classification"] == "infrastructure_exclusion":
                report["exclusions"].append(
                    {"attempt": number, "reason": record["error"] or "tool infrastructure invalid"}
                )
            report["attempted_tools"] += 1
            report["wall_s"] += elapsed
            report["attempt_ids"].append(identity)
            if operation == "run":
                report["runs"].append(
                    {
                        "attempt": number,
                        "attempt_id": identity,
                        "run_id": record.get("run_id"),
                        "classification": record["classification"],
                        "ok": record["ok"],
                        "result": copy.deepcopy(record.get("result")),
                        "process": copy.deepcopy(record.get("process")),
                        "preparation": copy.deepcopy(record.get("preparation")),
                        "result_path": (
                            f"{directory}/stdout.json" if record.get("result") is not None else None
                        ),
                        "error": record["error"],
                        "collection_path": (
                            f"{directory}/run/run-evidence.json" if collection is not None else None
                        ),
                        "collection": copy.deepcopy(collection),
                    }
                )
            attempts[number] = identity
            operations[number] = operation
        actual_directories = {
            path.name for path in output.glob("tool-*") if path.is_dir() or path.is_symlink()
        }
        if actual_directories != {f"tool-{number:06d}" for number in attempts}:
            raise ValueError("tool attempt directories do not match the journal")
        if (output / "tool-service.json").exists():
            service = decode(read("tool-service.json"))
            if not isinstance(service, dict) or any(
                service.get(key) != value
                for key, value in (
                    ("session_id", session_id),
                    ("plan_id", plan_id),
                    ("arm", arm),
                    ("schema_version", "aisle.matched-tool-service.v1"),
                )
            ):
                raise ValueError("tool service identity is unresolved")
            if (
                service.get("ok") is not True
                or service.get("pending_requests") != []
                or service.get("error") is not None
            ):
                raise ValueError("tool service failed or left pending requests")
            index = (
                lines("tool-request-index.jsonl")
                if (output / "tool-request-index.jsonl").exists()
                else []
            )
            if any(
                type(service.get(key)) is not int or service[key] != len(index)
                for key in ("processed_requests", "seen_requests")
            ) or len(index) != len(attempts):
                raise ValueError("tool request and attempt counts differ")
            seen = set()
            for number, link in enumerate(index, start=1):
                request_id = link["request_id"]
                if (
                    not isinstance(request_id, str)
                    or len(request_id) != 32
                    or any(c not in "0123456789abcdef" for c in request_id)
                    or request_id in seen
                ):
                    raise ValueError("tool request identity is invalid or repeated")
                seen.add(request_id)
                if link["attempt"] != number or link["attempt_id"] != attempts[number]:
                    raise ValueError("tool request points to a different attempt")
                name = f"request-{request_id}.json"
                request = decode(read(name))
                if report["files"][name] != link["request_sha256"] or request != {
                    "schema_version": "aisle.matched-tool-request.v1",
                    "id": request_id,
                    "operation": operations[number],
                }:
                    raise ValueError("tool request content or identity drift")
            retained_requests = {
                path.name for path in output.iterdir() if path.name.startswith("request-")
            }
            if retained_requests != {f"request-{identity}.json" for identity in seen}:
                raise ValueError("retained tool request inventory differs from the index")
            requires_authority = service.get("request_authority_required", False)
            if type(requires_authority) is not bool:
                raise ValueError("invalid request authority requirement")
            if (
                requires_authority
                or require_frontend_source
                or frontend_dispatch_ceiling is not None
                or request_authority is not None
                or any("frontend_authorization" in link for link in index)
            ):
                from aisle.harness.frontend_request_audit import verify_request_authorizations

                if (
                    type(request_authority) is not dict
                    or (
                        "mcp_harness" in request_authority
                        and not {"protocol", "provider", "dispatch"}.issubset(request_authority)
                    )
                    or set(request_authority) - {"mcp_harness"}
                    not in (
                        {"artifacts", "expected", "byte_limit"},
                        {"artifacts", "expected", "byte_limit", "protocol"},
                        {"artifacts", "expected", "byte_limit", "protocol", "dispatch"},
                        {"artifacts", "expected", "byte_limit", "protocol", "dispatch", "provider"},
                        {
                            "artifacts",
                            "expected",
                            "byte_limit",
                            "protocol",
                            "dispatch",
                            "provider",
                            "code_mode",
                        },
                        {
                            "artifacts",
                            "expected",
                            "byte_limit",
                            "protocol",
                            "dispatch",
                            "code_mode",
                        },
                    )
                    or type(request_authority["expected"]) is not dict
                    or request_authority["expected"].get("session_id") != session_id
                ):
                    raise ValueError("trusted request authority evidence is missing or mismatched")
                authorizations = verify_request_authorizations(
                    **{
                        key: request_authority[key]
                        for key in ("artifacts", "expected", "byte_limit")
                    },
                    links=index,
                    requests={identity: read(f"request-{identity}.json") for identity in seen},
                )
                if not authorizations["ok"]:
                    raise ValueError(
                        "request authorization audit failed: " + "; ".join(authorizations["errors"])
                    )
                report["frontend_authorization_verified"] = True
                protocol = request_authority.get("protocol")
                if (
                    require_frontend_source
                    or protocol is not None
                    or frontend_dispatch_ceiling is not None
                ):
                    from aisle.harness.frontend_app_server_audit import verify_app_server_sources

                    if type(protocol) is not dict or set(protocol) != {
                        "artifacts",
                        "expected",
                        "byte_limit",
                    }:
                        raise ValueError("trusted frontend source evidence is missing")
                    source = verify_app_server_sources(
                        **protocol,
                        grants=[link["frontend_authorization"] for link in index],
                        dispatch=request_authority.get("dispatch"),
                        dispatch_ceiling=frontend_dispatch_ceiling,
                        code_mode=request_authority.get("code_mode"),
                        provider=request_authority.get("provider"),
                        mcp_harness=request_authority.get("mcp_harness"),
                    )
                    if not source["ok"]:
                        raise ValueError(
                            "frontend source audit failed: " + "; ".join(source["errors"])
                        )
                    report["frontend_source_verified"] = True
                    report["frontend_reservation_verified"] = source["reservations_verified"]
            report["service_verified"] = True
        elif (
            request_authority is not None
            or require_frontend_source
            or frontend_dispatch_ceiling is not None
        ):
            raise ValueError("request authority evidence requires a service journal")
        report["ok"] = True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["error"] = str(exc)
    return report
