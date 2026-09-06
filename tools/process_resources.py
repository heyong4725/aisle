"""BMK-8/BMK-17 sampled process-tree RSS; gaps never imply zero memory."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable


def tree_rss(rows: str, root_pid: int, *, exclude_pid: int | None = None) -> dict:
    processes = {}
    for line in rows.splitlines():
        try:
            pid, parent, rss = map(int, line.split())
        except ValueError as exc:
            raise ValueError("unreadable process observation") from exc
        if min(pid, parent, rss) < 0 or pid in processes:
            raise ValueError("invalid or duplicate process observation")
        processes[pid] = (parent, rss)
    if root_pid not in processes:
        raise ValueError("measured root process is absent")
    included = {root_pid}
    while True:
        children = {
            pid
            for pid, (parent, _) in processes.items()
            if parent in included and pid != exclude_pid
        }
        updated = included | children
        if updated == included:
            break
        included = updated
    return {
        "rss_bytes": sum(processes[pid][1] * 1024 for pid in included),
        "processes": len(included),
    }


def observe_tree(root_pid: int) -> dict:
    with subprocess.Popen(
        ["ps", "-axo", "pid=,ppid=,rss="], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as proc:
        try:
            stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        if proc.returncode:
            raise ValueError("process inspection failed")
        return tree_rss(stdout, root_pid, exclude_pid=proc.pid)


class ResourceSampler:
    """Retain observed memory samples; this is not an exact instantaneous peak."""

    def __init__(
        self,
        *,
        observer: Callable | None = None,
        interval_s: float = 0.25,
        clock: Callable = time.monotonic,
    ):
        if not 0.05 <= interval_s <= 60:
            raise ValueError("sampling interval must be between 0.05 and 60 seconds")
        self.root_pid = os.getpid()
        self.observer = observer or (lambda: observe_tree(self.root_pid))
        self.interval_s = interval_s
        self.clock = clock
        self.started = clock()
        self.observations = []
        self.errors = []
        self._stop = threading.Event()
        self._thread = None

    def sample(self):
        try:
            reading = self.observer()
            if (
                not isinstance(reading, dict)
                or type(reading.get("rss_bytes")) is not int
                or reading["rss_bytes"] < 0
                or type(reading.get("processes")) is not int
                or reading["processes"] < 1
            ):
                raise ValueError("invalid memory observation")
            self.observations.append(
                {"elapsed_s": round(self.clock() - self.started, 6), **reading}
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.errors.append(type(exc).__name__ + ": process memory observation unavailable")

    def __enter__(self):
        self.sample()

        def run():
            while not self._stop.wait(self.interval_s):
                self.sample()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=6)
        if self._thread.is_alive():
            self.errors.append("observer did not stop within its timeout")
        else:
            self.sample()

    def report(self) -> dict:
        observations = list(self.observations)
        errors = list(self.errors)
        measured = bool(observations) and not errors
        return {
            "schema_version": "aisle.process-memory.v1",
            "status": "measured" if measured else "unmeasured",
            "method": "sampled_sum_of_process_tree_rss",
            "root_pid": self.root_pid,
            "sample_interval_s": self.interval_s,
            "sampled_peak_rss_bytes": max(r["rss_bytes"] for r in observations)
            if measured
            else None,
            "exact_peak": False,
            "samples": len(observations),
            "observations": observations,
            "errors": errors,
            "limitations": [
                "peaks between samples can be missed",
                "shared resident pages can be counted in multiple processes",
                "detached or reparented processes are outside the observed tree",
                "device memory is not measured",
            ],
        }
