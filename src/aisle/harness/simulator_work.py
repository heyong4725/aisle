"""Per-launch operation journals; producer coverage must be established separately.

Completed step work is a lower bound when a step fails or a journal is interrupted.
Operation wall time measures wrapped calls, not total process or session duration.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

_KINDS = {"build", "reset", "step"}
_SCHEMA = "aisle.simulator-work-journal.v1"
_RENDER_SCHEMA = "aisle.simulator-work-journal.v2"
_RENDER_KINDS = _KINDS | {"render"}


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _work_error(exc):
    """Preserve filesystem failure identity independently of archive location."""
    if isinstance(exc, OSError):
        return f"{type(exc).__name__} (errno {exc.errno})"
    return str(exc)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate simulator work field")
        result[key] = value
    return result


class WorkJournal:
    """Persist operation starts before invoking simulator code; never overwrite."""

    def __init__(self, path, *, run_id, launch, dt_ns, n_envs, clock=time.monotonic_ns):
        if not isinstance(run_id, str) or not run_id or not _integer(launch):
            raise ValueError("invalid simulator launch identity")
        if not _integer(dt_ns, 1) or not _integer(n_envs, 1):
            raise ValueError("invalid simulator step units")
        path = Path(path).absolute()
        if path.resolve() != path:
            raise ValueError("simulator journal path is redirected")
        self.stream = path.open("x", encoding="utf-8")
        self.clock, self.sequence, self.active = clock, 0, False
        try:
            self._write(
                {
                    "event": "launch",
                    "schema_version": _RENDER_SCHEMA,
                    "run_id": run_id,
                    "launch": launch,
                    "dt_ns": dt_ns,
                    "n_envs": n_envs,
                }
            )
        except BaseException:
            self.stream.close()
            raise

    def _write(self, record):
        self.stream.write(json.dumps(record, allow_nan=False) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def __enter__(self):
        return self

    def call(self, kind, function, *args, **kwargs):
        with self.operation(kind):
            return function(*args, **kwargs)

    @contextmanager
    def operation(self, kind):
        if kind not in _RENDER_KINDS or self.active or self.stream.closed:
            raise ValueError("invalid or overlapping simulator operation")
        self.sequence += 1
        self._write({"event": "started", "sequence": self.sequence, "kind": kind})
        self.active = True
        started = self.clock()
        outcome = "failed"
        try:
            yield
            outcome = "completed"
        finally:
            elapsed = self.clock() - started
            self.active = False
            if not _integer(elapsed):
                raise ValueError("simulator operation clock moved backward")
            self._write(
                {"event": outcome, "sequence": self.sequence, "kind": kind, "wall_ns": elapsed}
            )

    def __exit__(self, exc_type, exc, traceback):
        try:
            self._write(
                {
                    "event": "terminal",
                    "operations": self.sequence,
                    "outcome": "returned" if exc_type is None else "raised",
                }
            )
        finally:
            self.stream.close()
        return False


def summarize_work(path, *, run_id, launch):
    """Replay one expected launch, preserving known counts on incomplete input."""
    report = {
        "schema_version": _SCHEMA,
        "run_id": run_id,
        "launch": launch,
        "journal_complete": False,
        "recorded_step_work_exact": False,
        "attempted": dict.fromkeys(sorted(_KINDS), 0),
        "completed": dict.fromkeys(sorted(_KINDS), 0),
        "failed": dict.fromkeys(sorted(_KINDS), 0),
        "operation_wall_ns": dict.fromkeys(sorted(_KINDS), 0),
        "completed_env_steps": 0,
        "completed_env_sim_ns": 0,
        "error": None,
    }
    sequence, active, terminal = 0, None, False
    try:
        with Path(path).open("rb") as stream:
            header = None
            while raw := stream.readline(4097):
                if len(raw) > 4096 or not raw.endswith(b"\n"):
                    raise ValueError("incomplete or oversized simulator journal line")
                row = json.loads(raw, object_pairs_hook=_object)
                if type(row) is not dict or terminal:
                    raise ValueError("invalid simulator journal event")
                if header is None:
                    if (
                        set(row)
                        != {"event", "schema_version", "run_id", "launch", "dt_ns", "n_envs"}
                        or row["event"] != "launch"
                        or row["schema_version"] not in {_SCHEMA, _RENDER_SCHEMA}
                        or row["run_id"] != run_id
                        or not _integer(row["launch"])
                        or row["launch"] != launch
                        or not _integer(row["dt_ns"], 1)
                        or not _integer(row["n_envs"], 1)
                    ):
                        raise ValueError("simulator journal launch identity or units differ")
                    header = row
                    if row["schema_version"] == _RENDER_SCHEMA:
                        report["schema_version"] = _RENDER_SCHEMA
                        for key in ("attempted", "completed", "failed", "operation_wall_ns"):
                            report[key]["render"] = 0
                    continue
                event = row.get("event")
                if event == "terminal":
                    if (
                        set(row) != {"event", "operations", "outcome"}
                        or active is not None
                        or not _integer(row["operations"])
                        or row["operations"] != sequence
                        or row["outcome"] not in {"returned", "raised"}
                    ):
                        raise ValueError("invalid simulator terminal record")
                    terminal = True
                    continue
                fields = {"event", "sequence", "kind"}
                if event in {"completed", "failed"}:
                    fields.add("wall_ns")
                if (
                    set(row) != fields
                    or row.get("kind")
                    not in (_RENDER_KINDS if header["schema_version"] == _RENDER_SCHEMA else _KINDS)
                    or not _integer(row.get("sequence"), 1)
                ):
                    raise ValueError("invalid simulator operation fields")
                kind = row["kind"]
                if event == "started" and active is None and row["sequence"] == sequence + 1:
                    sequence += 1
                    active = (sequence, kind)
                    report["attempted"][kind] += 1
                elif (
                    event in {"completed", "failed"}
                    and active == (row["sequence"], kind)
                    and _integer(row["wall_ns"])
                ):
                    report[event][kind] += 1
                    report["operation_wall_ns"][kind] += row["wall_ns"]
                    if event == "completed" and kind == "step":
                        report["completed_env_steps"] += header["n_envs"]
                        report["completed_env_sim_ns"] += header["n_envs"] * header["dt_ns"]
                    active = None
                else:
                    raise ValueError("simulator operation lifecycle differs")
        if not terminal:
            raise ValueError("simulator terminal record is missing")
        report["journal_complete"] = True
        report["recorded_step_work_exact"] = report["failed"]["step"] == 0
    except (OSError, ValueError, TypeError) as exc:
        report["error"] = _work_error(exc)
    return report


class _DirectCalls:
    def operation(self, kind):
        return nullcontext()

    def call(self, kind, function, *args, **kwargs):
        return function(*args, **kwargs)


def work_context(environment, *, dt_ns, n_envs, clock=time.monotonic_ns):
    """Select complete controller-supplied accounting or the absent legacy option."""
    names = {"AISLE_SIM_WORK_PATH", "AISLE_SIM_WORK_RUN_ID", "AISLE_SIM_WORK_LAUNCH"}
    supplied = names.intersection(environment)
    if not supplied:
        return nullcontext(_DirectCalls())
    if supplied != names or any(not environment[name] for name in names):
        raise ValueError("simulator work binding must be complete")
    launch = environment["AISLE_SIM_WORK_LAUNCH"]
    if not isinstance(launch, str) or not launch.isascii() or not launch.isdecimal():
        raise ValueError("invalid simulator work launch index")
    path = Path(environment["AISLE_SIM_WORK_PATH"])
    if not path.is_absolute():
        raise ValueError("simulator work path must be absolute")
    return WorkJournal(
        path,
        run_id=environment["AISLE_SIM_WORK_RUN_ID"],
        launch=int(launch),
        dt_ns=dt_ns,
        n_envs=n_envs,
        clock=clock,
    )


def _graph_digest(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")


def _launch_receipt(run_id, launch, graph_sha256):
    if not isinstance(run_id, str) or not run_id or not _integer(launch):
        raise ValueError("invalid simulator launch identity")
    if not _graph_digest(graph_sha256):
        raise ValueError("invalid executed graph digest")
    return {
        "schema_version": "aisle.simulator-work-launch.v1",
        "run_id": run_id,
        "launch": launch,
        "graph_sha256": graph_sha256,
        "journal": f"launch-{launch}.jsonl",
    }


def reserve_launch(directory, *, run_id, launch, graph_sha256):
    """Durably declare an expected producer before spawning; never reuse a receipt.

    The controller must hash the graph containing the corresponding binding first.
    This receipt records a launch attempt, not proof that a process started.
    """
    receipt = _launch_receipt(run_id, launch, graph_sha256)
    directory = Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError("simulator work directory is absent or redirected")
    with (directory / f"launch-{launch}.expected.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return launch_binding(directory, run_id=run_id, launch=launch)


def launch_binding(directory, *, run_id, launch):
    """Construct graph-attested paths before hashing; this does not reserve a launch."""
    if not isinstance(run_id, str) or not run_id or not _integer(launch):
        raise ValueError("invalid simulator launch identity")
    directory = Path(directory).absolute()
    if directory.resolve() != directory:
        raise ValueError("simulator work directory is redirected")
    return {
        "AISLE_SIM_WORK_PATH": str(directory / f"launch-{launch}.jsonl"),
        "AISLE_SIM_WORK_RUN_ID": run_id,
        "AISLE_SIM_WORK_LAUNCH": str(launch),
    }


def summarize_launches(directory, *, run_id, expected_graph_hashes):
    """Reconcile journals against the controller's ordered launch expectations.

    Explicit step totals are lower bounds unless every expected journal closes.
    Full simulator producer coverage is a separate obligation, never inferred here.
    """
    if not isinstance(expected_graph_hashes, (list, tuple)):
        raise ValueError("expected graph hashes must be an ordered sequence")
    expected = [
        _launch_receipt(run_id, index, digest) for index, digest in enumerate(expected_graph_hashes)
    ]
    directory = Path(directory).absolute()
    report = {
        "schema_version": "aisle.simulator-work-summary.v1",
        "run_id": run_id,
        "journals_complete": False,
        "recorded_step_work_exact": False,
        "producer_coverage_complete": False,
        "completed_env_steps": 0,
        "completed_env_sim_ns": 0,
        "launches": [],
        "errors": [],
    }
    try:
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError("simulator work directory is absent or redirected")
        names = {
            name
            for row in expected
            for name in (row["journal"], f"launch-{row['launch']}.expected.json")
        }
        if any(p.name not in names for p in directory.iterdir()):
            report["errors"].append("unexpected simulator work artifact")
        for row in expected:
            index = row["launch"]
            try:
                receipt = directory / f"launch-{index}.expected.json"
                journal = directory / row["journal"]
                if receipt.resolve() != receipt or journal.resolve() != journal:
                    raise ValueError("simulator launch artifact is redirected")
                with receipt.open("rb") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError("oversized simulator launch receipt")
                captured = json.loads(raw, object_pairs_hook=_object)
                if (
                    type(captured) is not dict
                    or type(captured.get("launch")) is not int
                    or captured != row
                ):
                    raise ValueError("simulator launch receipt differs from controller expectation")
                result = summarize_work(journal, run_id=run_id, launch=index)
                report["completed_env_steps"] += result["completed_env_steps"]
                report["completed_env_sim_ns"] += result["completed_env_sim_ns"]
            except (OSError, ValueError, TypeError) as exc:
                result = {
                    "launch": index,
                    "journal_complete": False,
                    "recorded_step_work_exact": False,
                    "error": _work_error(exc),
                }
            report["launches"].append(result)
        report["journals_complete"] = (
            bool(expected)
            and not report["errors"]
            and all(row["journal_complete"] for row in report["launches"])
        )
        report["recorded_step_work_exact"] = report["journals_complete"] and all(
            row["recorded_step_work_exact"] for row in report["launches"]
        )
    except (OSError, ValueError, TypeError) as exc:
        report["errors"].append(_work_error(exc))
    return report
