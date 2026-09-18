"""Hidden-access log collected from the macOS sandbox's own denial reports.

The sandbox reports the denials it enforces as kernel log messages
(`Sandbox: <name>(<pid>) deny(<n>) <operation> <target>`). They are visible
live through `log stream` but are not reliably persisted, so the controller
runs a stream sidecar for the whole launch window and turns the reports that
belong to the session's own processes into the postflight's hidden-access log
(TRT-6): every event is a denial the sandbox itself observed, classified
against the session's authority roots.

Reporting is not guaranteed. On the development host the kernel reported
every denial by Apple-signed binaries but, after a burst of earlier sessions,
none by the third-party interpreter the harness admits, while still enforcing
them. A log can therefore only claim completeness through positive controls:
at the start and at the end of the window the controller runs the session's
own admitted executable under the session profile against a controller-owned
canary whose read is denied, and requires the sandbox to report exactly that
denial. A missing marker or a selected process name without a marker fails
closed (`complete: false`); a collector built without markers can never claim
completeness, whatever the stream did.
The kernel reports the first occurrence of a denial as its own line and
coalesces further identical ones into `N duplicate reports for ...`, so a
summary weighs N events and a plain report one.

Reports are selected by process name (the basenames the session's policies
admit). The kernel does not tell one session's interpreter from another's,
so the controller runs one session at a time and no capability audit during
a session; the retained pids let a reviewer cross-check the selection. Allows
are never reported, so `visible_allows` is structurally zero. The retained
log keeps only the postflight's four keys; the companion collection record
keeps the window, the selection, counts, pids and the marker outcomes, never
targets.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aisle.hidden-access-log.v1"
COLLECTION_SCHEMA_VERSION = "aisle.hidden-access-collection.v1"
LOG_NAME = "hidden-access-log.json"
COLLECTION_NAME = "hidden-access-collection.json"
STREAM_COMMAND = (
    "/usr/bin/log",
    "stream",
    "--style",
    "ndjson",
    "--info",
    "--predicate",
    'sender == "Sandbox"',
)
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
#: the kernel keeps 32 bytes of a process name; longer basenames never match
PROCESS_NAME_LIMIT = 32
#: duplicate-report summaries land about two seconds after their process exits
DEFAULT_GRACE_S = 5.0
#: how long a marker's denial may take to surface in the stream
MARKER_TIMEOUT_S = 10.0
_REPORT = re.compile(
    r"^(?:(?P<duplicates>\d+) duplicate reports? for )?"
    r"Sandbox: (?P<process>.+?)\((?P<pid>\d+)\) deny\((?P<count>\d+)\) "
    r"(?P<operation>\S+)(?: (?P<target>.*))?$"
)
_PYTHON_READER = "import sys\nopen(sys.argv[1], 'rb').read()\n"


@dataclass(frozen=True)
class AuthorityRoots:
    """The roots a session's policy lets the confined process read or write,
    and the roots it hides; anything else is outside the arm's authority."""

    readable: tuple[Path, ...]
    hidden: tuple[Path, ...]


def parse_report(message: Any) -> dict[str, Any] | None:
    """The fields of one sandbox denial report, or None for any other line."""
    if type(message) is not str:
        return None
    match = _REPORT.match(message)
    if match is None:
        return None
    return {
        "process": match["process"],
        "pid": int(match["pid"]),
        "operation": match["operation"],
        "target": match["target"] or "",
        "duplicates": int(match["duplicates"] or 0),
    }


def classify(operation: str, target: str, roots: AuthorityRoots) -> str:
    """`visible` for a denied file operation on a path inside a readable root;
    `hidden` for a path inside a hidden root, a path outside every root, and
    any non-file operation (network, mach lookups, process execution)."""
    return "visible" if _target_kind(operation, target, roots) == "visible" else "hidden"


def _target_kind(operation: str, target: str, roots: AuthorityRoots) -> str:
    if not operation.startswith("file-") or not target.startswith("/"):
        return "non_file"
    path = Path(target)
    # hidden roots win, so an overlapping declaration never under-reports
    if any(path == root or path.is_relative_to(root) for root in roots.hidden):
        return "hidden_root"
    if any(path == root or path.is_relative_to(root) for root in roots.readable):
        return "visible"
    return "outside_roots"


def process_names(policies: list[dict[str, Any]]) -> set[str]:
    """Basenames of every executable the given canonical policies admit,
    truncated the way the kernel truncates them in its reports."""
    names: set[str] = set()
    for policy in policies:
        executables = policy.get("allowed_executables") if isinstance(policy, dict) else None
        if not isinstance(executables, list) or any(type(p) is not str for p in executables):
            raise ValueError("policy must carry a list of allowed executables")
        names.update(Path(p).name[:PROCESS_NAME_LIMIT] for p in executables)
    return names


def marker_command(executables: list[str] | tuple[str, ...]) -> list[str] | None:
    """An argv prefix, drawn from the session's own admitted executables, that
    reads the path appended to it: the first admitted interpreter, else the
    shell, else cat. None when the policy admits none of them."""
    resolved = [str(p) for p in executables]
    for path in resolved:
        if Path(path).name.startswith("python"):
            return [path, "-I", "-c", _PYTHON_READER]
    for path in resolved:
        if Path(path).name == "bash":
            return [path, "-c", 'exec 3<"$1"', "aisle-marker"]
    for path in resolved:
        if Path(path).name == "cat":
            return [path]
    return None


def _row(line: str) -> Any:
    """One stream row decoded, or None for the stream's plain-text header."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _report_in(row: Any) -> dict[str, Any] | None:
    """The denial report one decoded row carries, or None."""
    return parse_report(row.get("eventMessage") if isinstance(row, dict) else None)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SandboxReportCollector:
    """Stream the sandbox's denial reports for the duration of a launch and
    retain the session's hidden-access log on exit, even when the launch raises."""

    def __init__(
        self,
        *,
        names: set[str],
        roots: AuthorityRoots,
        output: Path,
        adapter_active: bool,
        profile_path: Path | None = None,
        marker: list[str] | None = None,
        command: list[str] | tuple[str, ...] | None = None,
        start_timeout_s: float = 10.0,
        marker_timeout_s: float | None = None,
        grace_s: float = DEFAULT_GRACE_S,
        term_timeout_s: float = 5.0,
        now: Callable[[], str] | None = None,
    ) -> None:
        if (profile_path is None) != (marker is None):
            raise ValueError("positive-control markers need both the profile and a marker command")
        self.names = frozenset(names)
        self.roots = roots
        self.output = Path(output)
        self.adapter_active = bool(adapter_active)
        self.profile_path = None if profile_path is None else Path(profile_path)
        self.marker = None if marker is None else list(marker)
        self.command = list(command or STREAM_COMMAND)
        self.start_timeout_s = start_timeout_s
        self.marker_timeout_s = MARKER_TIMEOUT_S if marker_timeout_s is None else marker_timeout_s
        self.grace_s = grace_s
        self.term_timeout_s = term_timeout_s
        self.now = now or _now
        self.started = False
        self.markers: dict[str, bool] | None = (
            None if marker is None else {"start": False, "end": False}
        )
        self._process: subprocess.Popen | None = None
        self._lines: list[str] = []
        self._lines_lock = threading.Lock()
        self._line_or_eof = threading.Event()
        self._reader: threading.Thread | None = None
        self._reader_failed: str | None = None
        self._started_at: str | None = None
        self._died_early = False
        self._canary_dir: tempfile.TemporaryDirectory | None = None
        self._canaries: list[str] = []

    @property
    def log_path(self) -> Path:
        return self.output / LOG_NAME

    def _read(self, stream) -> None:
        try:
            for line in stream:
                with self._lines_lock:
                    self._lines.append(line)
                self._line_or_eof.set()
        except Exception as exc:  # noqa: BLE001 - recorded, never silently lost
            self._reader_failed = f"{type(exc).__name__}: {exc}"
        finally:
            self._line_or_eof.set()

    def _seen(self, canary: str) -> bool:
        """Whether the stream has reported a denial on `canary` (the stream
        escapes paths in its JSON, so rows are parsed, never searched)."""
        with self._lines_lock:
            lines = list(self._lines)
        for line in lines:
            report = _report_in(_row(line))
            if report is not None and self._is_marker_report(report, canary):
                return True
        return False

    def _is_marker_report(self, report: dict[str, Any], canary: str) -> bool:
        return (
            self.marker is not None
            and report["target"] == canary
            and report["process"] == Path(self.marker[0]).name[:PROCESS_NAME_LIMIT]
            and report["operation"].startswith("file-read")
        )

    def _run_marker(self, name: str) -> bool:
        """Read a fresh controller-owned canary through the session's own
        executable under the session profile and require the sandbox to report
        that exact denial."""
        if self.marker is None or self._canary_dir is None:
            return False
        canary = Path(self._canary_dir.name) / f"{name}-{secrets.token_hex(8)}"
        self._canaries.append(str(canary))
        try:
            canary.write_bytes(b"AISLE-CANARY\n")
            subprocess.run(
                [str(SANDBOX_EXEC), "-f", str(self.profile_path), *self.marker, str(canary)],
                capture_output=True,
                timeout=self.marker_timeout_s,
                cwd=self._canary_dir.name,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        deadline = time.monotonic() + self.marker_timeout_s
        while time.monotonic() < deadline:
            if self._seen(str(canary)):
                return True
            time.sleep(0.05)
        return self._seen(str(canary))

    def __enter__(self) -> SandboxReportCollector:
        self.output.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists() or (self.output / COLLECTION_NAME).exists():
            raise FileExistsError(f"hidden-access log already retained under {self.output}")
        self._process = subprocess.Popen(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            self._reader = threading.Thread(
                target=self._read, args=(self._process.stdout,), daemon=True
            )
            self._reader.start()
            # the stream announces itself with a header line before any report;
            # a window that opens without it cannot claim completeness
            self.started = self._line_or_eof.wait(self.start_timeout_s) and bool(self._lines)
            self._started_at = self.now()
            if self.marker is not None:
                canary_parent = "/private/tmp" if Path("/private/tmp").is_dir() else None
                self._canary_dir = tempfile.TemporaryDirectory(
                    prefix="aisle-canary-", dir=canary_parent
                )
                self.markers["start"] = self.started and self._run_marker("start")
        except BaseException:
            self._process.kill()
            self._process.wait(timeout=5)
            self._discard_canaries()
            raise
        return self

    def _discard_canaries(self) -> None:
        if self._canary_dir is not None:
            with contextlib.suppress(OSError):
                self._canary_dir.cleanup()

    def _stop(self) -> int | None:
        process = self._process
        if process is None:
            return None
        if process.poll() is not None:
            self._died_early = True
        else:
            time.sleep(self.grace_s)
            if process.poll() is not None:
                self._died_early = True
            else:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=self.term_timeout_s)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.term_timeout_s)
        if self._reader is not None:
            self._reader.join(timeout=5)
        return process.returncode

    def __exit__(self, exc_type, exc, tb) -> None:
        """Run the end marker, stop the stream and always retain the log: a
        failure anywhere in the teardown is recorded as an incomplete window,
        never as a missing record."""
        returncode = None
        stop_failed: str | None = None
        try:
            if self.markers is not None and self._process is not None:
                self.markers["end"] = (
                    self._process.poll() is None and self.started and self._run_marker("end")
                )
            returncode = self._stop()
        except Exception as exc:  # noqa: BLE001 - recorded, never silently lost
            stop_failed = f"{type(exc).__name__}: {exc}"
            if self._process is not None and self._process.poll() is None:
                with contextlib.suppress(OSError):
                    self._process.kill()
        finally:
            self._discard_canaries()
        finished_at = self.now()
        reader_ok = (
            self._reader_failed is None and self._reader is not None and not self._reader.is_alive()
        )
        ended_cleanly = (
            stop_failed is None
            and not self._died_early
            and reader_ok
            and returncode in (0, -signal.SIGTERM)
        )
        marker_name = (
            None if self.marker is None else Path(self.marker[0]).name[:PROCESS_NAME_LIMIT]
        )
        unprobed_names = sorted(self.names - ({marker_name} if marker_name is not None else set()))
        complete = (
            self.started
            and ended_cleanly
            and self.markers is not None
            and all(self.markers.values())
            and marker_name in self.names
            and not unprobed_names
        )
        events: list[dict[str, str]] = []
        pids: dict[str, set[int]] = {}
        counts = {
            "reports": 0,
            "selected": 0,
            "unselected": 0,
            "unparsed": 0,
            "markers": 0,
            "events": 0,
        }
        kinds = {"hidden_root": 0, "outside_roots": 0, "non_file": 0, "visible": 0}
        with self._lines_lock:
            lines = list(self._lines)
        for line in lines:
            row = _row(line)
            if row is None:
                continue  # the stream's own header, not a row
            report = _report_in(row)
            if report is None:
                counts["unparsed"] += 1
                continue
            counts["reports"] += 1
            if any(self._is_marker_report(report, canary) for canary in self._canaries):
                counts["markers"] += 1
                continue
            if report["process"] not in self.names:
                counts["unselected"] += 1
                continue
            counts["selected"] += 1
            pids.setdefault(report["process"], set()).add(report["pid"])
            kind = _target_kind(report["operation"], report["target"], self.roots)
            weight = report["duplicates"] or 1
            kinds[kind] += weight
            event = {
                "decision": "deny",
                "surface": report["operation"],
                "target_class": "visible" if kind == "visible" else "hidden",
            }
            events.extend([event] * weight)
        counts["events"] = len(events)
        log = {
            "schema_version": SCHEMA_VERSION,
            "adapter_active": self.adapter_active,
            "complete": complete,
            "events": events,
        }
        collection = {
            "schema_version": COLLECTION_SCHEMA_VERSION,
            "command": self.command,
            "window": {"started_at": self._started_at, "finished_at": finished_at},
            "selection": {"process_names": sorted(self.names)},
            "coverage": {
                "marker_process_name": marker_name,
                "unprobed_process_names": unprobed_names,
            },
            "counts": counts,
            "target_kinds": kinds,
            "pids": {name: sorted(values) for name, values in sorted(pids.items())},
            "markers": self.markers,
            "stream": {
                "started": self.started,
                "ended_cleanly": ended_cleanly,
                "returncode": returncode,
                "reader_failed": self._reader_failed,
                "stop_failed": stop_failed,
            },
        }
        with self.log_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(log, indent=2, sort_keys=True) + "\n")
        with (self.output / COLLECTION_NAME).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(collection, indent=2, sort_keys=True) + "\n")
