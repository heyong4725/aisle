"""Hidden-access log collected from the macOS sandbox's own denial reports.

TRT-6 (hidden material denied and observed), TRT-3, CON-8, CON-5.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from aisle.harness import hidden_access_log as hal

pytestmark = pytest.mark.unit


def _roots(tmp_path: Path) -> hal.AuthorityRoots:
    visible = tmp_path / "view"
    output = tmp_path / "scratch"
    hidden = tmp_path / "controller"
    for path in (visible, output, hidden):
        path.mkdir()
    return hal.AuthorityRoots(
        readable=(visible.resolve(), output.resolve(), Path("/usr/lib")),
        hidden=(hidden.resolve(),),
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Sandbox: cat(4242) deny(1) file-read-data /private/etc/hosts",
            {
                "process": "cat",
                "pid": 4242,
                "operation": "file-read-data",
                "target": "/private/etc/hosts",
                "duplicates": 0,
            },
        ),
        (
            "3 duplicate reports for Sandbox: bash(7) deny(1) network-outbound remote:*:9",
            {
                "process": "bash",
                "pid": 7,
                "operation": "network-outbound",
                "target": "remote:*:9",
                "duplicates": 3,
            },
        ),
        (
            "Sandbox: codex(99) deny(1) mach-lookup com.apple.diagnosticd",
            {
                "process": "codex",
                "pid": 99,
                "operation": "mach-lookup",
                "target": "com.apple.diagnosticd",
                "duplicates": 0,
            },
        ),
        (
            "Sandbox: python3.13(5) deny(1) process-exec /usr/bin/printf",
            {
                "process": "python3.13",
                "pid": 5,
                "operation": "process-exec",
                "target": "/usr/bin/printf",
                "duplicates": 0,
            },
        ),
        ("unrelated kernel chatter", None),
        ("Sandbox: cat(1) allow(1) file-read-data /x", None),
    ],
)
def test_denial_reports_are_parsed_exactly(message, expected):
    """TRT-6: the kernel's `Sandbox: name(pid) deny(n) op target` line is the only
    input; anything else is ignored, never guessed at."""
    assert hal.parse_report(message) == expected


def test_targets_are_classified_against_the_session_authority(tmp_path: Path):
    """TRT-6: a denied path inside a readable root is a visible-target denial; a
    denied hidden path, a path outside every root, and any non-file operation
    are hidden-target denials (outside the arm's authority)."""
    roots = _roots(tmp_path)
    visible, hidden = roots.readable[0], roots.hidden[0]
    assert hal.classify("file-write-data", str(visible / "graph.yaml"), roots) == "visible"
    assert hal.classify("file-read-data", str(hidden / "secret.txt"), roots) == "hidden"
    assert hal.classify("file-read-data", "/private/etc/hosts", roots) == "hidden"
    assert hal.classify("file-read-data", str(visible) + "-sibling/x", roots) == "hidden"
    assert hal.classify("network-outbound", "remote:*:9", roots) == "hidden"
    assert hal.classify("mach-lookup", "com.apple.diagnosticd", roots) == "hidden"
    overlapping = hal.AuthorityRoots(readable=(visible,), hidden=(visible / "nested",))
    assert hal.classify("file-read-data", str(visible / "nested" / "x"), overlapping) == "hidden"


def _fake_log(
    tmp_path: Path,
    lines: list[dict],
    *,
    header: bool = True,
    crash: bool = False,
    sigterm: str = "exit",
) -> list[str]:
    """A stand-in for `log stream`: prints a header, the given ndjson rows, then
    waits to be terminated (or exits at once when `crash`)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "fake-log.py"
    rows = tmp_path / "fake-log-rows.json"
    rows.write_text(json.dumps(lines))
    script.write_text(
        textwrap.dedent(
            f"""
            import json, signal, sys, time
            if {header!r}:
                print("Filtering the log data using a predicate", flush=True)
            for row in json.load(open({str(rows)!r})):
                print(json.dumps(row), flush=True)
            if {crash!r}:
                sys.exit(3)
            if {sigterm!r} == "exit":
                signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
            elif {sigterm!r} == "ignore":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(0.05)
            """
        )
    )
    return [sys.executable, "-u", str(script)]


def _row(message: str, process: str = "kernel") -> dict:
    return {
        "eventMessage": message,
        "senderImagePath": "/System/Library/Extensions/Sandbox.kext/Contents/MacOS/Sandbox",
        "processImagePath": "/" + process,
        "timestamp": "2026-09-14 09:57:25.682338-0500",
        "messageType": "Error",
    }


def test_collector_retains_selected_denials_and_provenance(tmp_path: Path):
    """TRT-6/CON-8: only denials from the session's admitted process names are
    events, a duplicate summary weighing its N further occurrences; the log
    carries exactly the postflight's four keys; the collection record retains
    the window, the selection, the unselected count and pids."""
    roots = _roots(tmp_path)
    hidden = roots.hidden[0]
    lines = [
        _row(f"Sandbox: cat(11) deny(1) file-read-data {hidden}/secret.txt"),
        _row("Sandbox: cat(11) deny(1) network-outbound remote:*:9"),
        _row(
            "2 duplicate reports for Sandbox: python3.13(12) deny(1) "
            f"file-write-data {roots.readable[0]}/x"
        ),
        _row("Sandbox: CGPDFService(83462) deny(1) mach-lookup com.apple.diagnosticd"),
        {"eventMessage": "noise", "senderImagePath": "/x", "timestamp": "t"},
        _row("Sandbox: cat(13) deny(1) file-read-data /private/etc/hosts", process="other"),
    ]
    output = tmp_path / "collection"
    with hal.SandboxReportCollector(
        names={"cat", "python3.13", "codex"},
        roots=roots,
        output=output,
        adapter_active=True,
        command=_fake_log(tmp_path, lines),
        grace_s=0.3,
    ) as collector:
        assert collector.started
    log = json.loads((output / "hidden-access-log.json").read_text())
    assert set(log) == {"adapter_active", "complete", "events", "schema_version"}
    assert log["schema_version"] == "aisle.hidden-access-log.v1"
    # no markers were run, so the log cannot claim completeness
    assert log["adapter_active"] is True and log["complete"] is False
    assert log["events"] == [
        {"decision": "deny", "surface": "file-read-data", "target_class": "hidden"},
        {"decision": "deny", "surface": "network-outbound", "target_class": "hidden"},
        {"decision": "deny", "surface": "file-write-data", "target_class": "visible"},
        {"decision": "deny", "surface": "file-write-data", "target_class": "visible"},
        {"decision": "deny", "surface": "file-read-data", "target_class": "hidden"},
    ]
    record = json.loads((output / "hidden-access-collection.json").read_text())
    assert record["schema_version"] == "aisle.hidden-access-collection.v1"
    assert record["selection"]["process_names"] == ["cat", "codex", "python3.13"]
    assert record["counts"] == {
        "reports": 5,
        "selected": 4,
        "unselected": 1,
        "unparsed": 1,
        "markers": 0,
        "events": 5,
    }
    assert record["target_kinds"] == {
        "hidden_root": 1,
        "outside_roots": 1,
        "non_file": 1,
        "visible": 2,
    }
    assert record["pids"] == {"cat": [11, 13], "python3.13": [12]}
    assert record["markers"] is None
    assert record["window"]["started_at"] < record["window"]["finished_at"]
    assert record["stream"] == {
        "started": True,
        "ended_cleanly": True,
        "returncode": 0,
        "reader_failed": None,
        "stop_failed": None,
    }
    assert str(hidden) not in json.dumps(log) and str(hidden) not in json.dumps(record)
    assert "/private/etc/hosts" not in json.dumps(record)


def test_collector_marks_the_log_incomplete_when_the_stream_never_started_or_died(tmp_path: Path):
    """TRT-6: a stream that produced no header before the launch, or that died
    during it, cannot claim completeness; the events it did see are kept."""
    roots = _roots(tmp_path)
    output = tmp_path / "collection"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=output,
        adapter_active=True,
        command=_fake_log(
            tmp_path, [_row("Sandbox: cat(1) deny(1) file-read-data /etc/hosts")], crash=True
        ),
        start_timeout_s=2.0,
    ) as collector:
        pass
    log = json.loads((output / "hidden-access-log.json").read_text())
    assert log["complete"] is False and len(log["events"]) == 1
    record = json.loads((output / "hidden-access-collection.json").read_text())
    assert record["stream"]["ended_cleanly"] is False and record["stream"]["returncode"] == 3
    output2 = tmp_path / "collection-2"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=output2,
        adapter_active=False,
        command=_fake_log(tmp_path / "nohead", [], header=False),
        start_timeout_s=0.5,
        grace_s=0.3,
    ) as collector:
        assert collector.started is False
    log = json.loads((output2 / "hidden-access-log.json").read_text())
    assert log == {
        "schema_version": "aisle.hidden-access-log.v1",
        "adapter_active": False,
        "complete": False,
        "events": [],
    }


def test_collector_writes_the_log_even_when_the_launch_raises(tmp_path: Path):
    """TRT-6: a launcher failure inside the collector still leaves a retained log
    so postflight can classify the attempt."""
    roots = _roots(tmp_path)
    output = tmp_path / "collection"
    with pytest.raises(RuntimeError, match="launcher"):
        with hal.SandboxReportCollector(
            names={"cat"},
            roots=roots,
            output=output,
            adapter_active=True,
            command=_fake_log(tmp_path, []),
            grace_s=0.3,
        ):
            raise RuntimeError("launcher failed")
    assert (output / "hidden-access-log.json").exists()
    with pytest.raises(FileExistsError):
        with hal.SandboxReportCollector(
            names={"cat"},
            roots=roots,
            output=output,
            adapter_active=True,
            command=_fake_log(tmp_path, []),
            grace_s=0.3,
        ):
            pass


def test_process_names_come_from_every_admitted_executable(tmp_path: Path):
    """TRT-6: the selection is the basenames of every executable the session's
    policies admit (arm, validator, workers); nothing is guessed from argv."""
    policies = [
        {"allowed_executables": ["/usr/bin/codex", "/bin/bash", "/opt/venv/bin/python3.13"]},
        {"allowed_executables": ["/opt/venv/bin/python3.13"]},
    ]
    assert hal.process_names(policies) == {"bash", "codex", "python3.13"}
    long_name = "x" * 40
    assert hal.process_names([{"allowed_executables": [f"/opt/{long_name}"]}]) == {"x" * 32}
    with pytest.raises(ValueError, match="policy"):
        hal.process_names([{"allowed_executables": "/bin/bash"}])


@pytest.mark.skipif(
    sys.platform != "darwin" or not Path("/usr/bin/log").exists(),
    reason="the unified log is macOS-only",
)
def test_live_collector_sees_a_real_sandbox_denial(tmp_path: Path):
    """TRT-6: a confined read of a hidden path under a real profile is reported by
    the sandbox and lands as a hidden-target denial with a complete log."""
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    roots = _roots(tmp_path)
    visible, output_root, hidden = roots.readable[0], roots.readable[1], roots.hidden[0]
    (hidden / "secret.txt").write_text("HIDDEN\n")
    policy = MacOSPolicy(
        visible_roots=(visible,),
        output_roots=(output_root,),
        runtime_read_roots=tuple(Path(p).resolve() for p in ("/bin", "/usr/lib", "/System")),
        allowed_executables=(Path("/bin/cat").resolve(),),
        hidden_roots=(hidden,),
        network_policy="deny-external",
    )
    profile = tmp_path / "profile.sb"
    profile.write_text(compile_macos_profile(policy).text)
    output = tmp_path / "collection"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=output,
        adapter_active=True,
        profile_path=profile,
        marker=hal.marker_command(["/bin/cat"]),
        grace_s=3.0,
    ) as collector:
        assert collector.started and collector.markers == {"start": True, "end": False}
        result = subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(profile), "/bin/cat", str(hidden / "secret.txt")],
            capture_output=True,
            timeout=15,
        )
        assert result.returncode != 0 and b"HIDDEN" not in result.stdout
    log = json.loads((output / "hidden-access-log.json").read_text())
    assert log["complete"] is True
    assert {"decision": "deny", "surface": "file-read-data", "target_class": "hidden"} in log[
        "events"
    ]
    assert "HIDDEN" not in json.dumps(log)
    record = json.loads((output / "hidden-access-collection.json").read_text())
    assert record["markers"] == {"start": True, "end": True}
    assert record["counts"]["markers"] >= 2
    assert "aisle-canary" not in json.dumps(record) and "aisle-canary" not in json.dumps(log)


@pytest.mark.skipif(
    sys.platform != "darwin" or not Path("/usr/bin/log").exists(),
    reason="the unified log is macOS-only",
)
def test_live_markers_fail_closed_when_the_sandbox_reports_nothing(tmp_path: Path):
    """TRT-6: a marker whose read the profile allows produces no denial report,
    so the positive control fails and the log cannot claim completeness even
    though the stream ran cleanly; the canary is removed afterwards."""
    profile = tmp_path / "permissive.sb"
    profile.write_text("(version 1)\n(allow default)\n")
    output = tmp_path / "collection"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=_roots(tmp_path),
        output=output,
        adapter_active=True,
        profile_path=profile,
        marker=hal.marker_command(["/bin/cat"]),
        marker_timeout_s=1.0,
        grace_s=0.3,
    ) as collector:
        assert collector.started and collector.markers == {"start": False, "end": False}
        canaries = list(Path("/private/tmp").glob("aisle-canary-*"))
        assert any(
            str(c) == collector._canary_dir.name
            for c in canaries  # noqa: SLF001
        )
    log = json.loads((output / "hidden-access-log.json").read_text())
    assert log["complete"] is False and log["events"] == []
    record = json.loads((output / "hidden-access-collection.json").read_text())
    assert record["stream"]["ended_cleanly"] is True
    assert record["markers"] == {"start": False, "end": False}
    assert not Path(collector._canary_dir.name).exists()  # noqa: SLF001


def test_marker_commands_come_from_the_admitted_executables():
    """TRT-6: the positive control runs the session's own admitted executable
    (interpreter first, then shell, then cat) so its report proves the kernel
    reports that identity; a policy admitting none of them cannot be collected."""
    python = hal.marker_command(["/usr/bin/codex", "/bin/bash", "/opt/venv/bin/python3.13"])
    assert python[0] == "/opt/venv/bin/python3.13" and python[1] == "-I"
    assert hal.marker_command(["/usr/bin/codex", "/bin/cat", "/bin/bash"])[0] == "/bin/bash"
    assert hal.marker_command(["/bin/cat"]) == ["/bin/cat"]
    assert hal.marker_command(["/usr/bin/codex", "/usr/bin/git"]) is None
    with pytest.raises(ValueError, match="markers"):
        hal.SandboxReportCollector(
            names={"cat"},
            roots=hal.AuthorityRoots(readable=(), hidden=()),
            output=Path("/nonexistent"),
            adapter_active=True,
            marker=["/bin/cat"],
        )


def test_fake_stream_markers_fail_closed_without_a_report(tmp_path: Path, monkeypatch):
    """TRT-6: a marker the stream never reports (here: a stand-in stream that
    cannot see the kernel) leaves both controls false and the log incomplete."""
    monkeypatch.setattr(hal, "SANDBOX_EXEC", Path(sys.executable))
    output = tmp_path / "collection"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=_roots(tmp_path),
        output=output,
        adapter_active=True,
        profile_path=tmp_path / "unused.sb",
        marker=["-c", "pass", "--"],
        marker_timeout_s=0.2,
        command=_fake_log(tmp_path, []),
        grace_s=0.3,
    ):
        pass
    log = json.loads((output / "hidden-access-log.json").read_text())
    assert log["complete"] is False
    record = json.loads((output / "hidden-access-collection.json").read_text())
    assert record["markers"] == {"start": False, "end": False}
    assert record["stream"]["ended_cleanly"] is True


def test_engineering_session_collects_its_own_access_log(tmp_path: Path, monkeypatch):
    """TRT-6/MON-12: a run request that asks for collection retains the sandbox's
    denial reports as the session's hidden-access log, with the collection record
    beside it, and postflight classifies the attempt from that log."""
    import hashlib

    from test_matched_session import (
        ROOT,
        _ambient_pair,
        _confinement_pair,
        _launch_pair,
        prepared_pair,
    )
    from test_treatment_confinement import _attestation

    sys.path.insert(0, str(ROOT / "tools"))
    import campaign
    from matched_campaign import run_engineering_session

    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    monkeypatch.setattr(campaign, "POLL_S", 0.01)
    monkeypatch.setattr(hal, "MARKER_TIMEOUT_S", 0.2)
    control, candidates, roots = prepared_pair(tmp_path)
    launches = _launch_pair(candidates)
    for launch in launches.values():
        launch["argv"][2] = (
            "import sys,json; "
            "print(json.dumps({'type':'item.completed','item':{'id':'prompt','type':"
            "'agent_message','text':sys.argv[1]+'\\n'+sys.argv[2]}})); raise SystemExit(0)"
        )
    bindings = _confinement_pair(tmp_path, control, candidates, roots)
    ambient = _ambient_pair(candidates, bindings)
    adapter = tmp_path / "fixture-adapter"
    adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    adapter.chmod(0o755)
    for candidate in candidates.values():
        candidate["confinement"]["adapter_binary_sha256"] = hashlib.sha256(
            adapter.read_bytes()
        ).hexdigest()
    policy = MacOSPolicy.from_canonical(bindings["typed"]["policy"])
    compiled = compile_macos_profile(policy)
    profile = tmp_path / "fixture.sb"
    profile.write_text(compiled.text)
    attestation = _attestation(compiled, profile, adapter)
    plan = admit_pair(
        control, candidates, roots, confinement=bindings, ambient=ambient, launches=launches
    )
    hidden = policy.hidden_roots[0]
    python_name = Path(sys.executable).resolve().name
    monkeypatch.setattr(
        hal,
        "STREAM_COMMAND",
        _fake_log(
            tmp_path / "fake",
            [_row(f"Sandbox: {python_name}(5) deny(1) file-read-data {hidden}/secret.txt")],
        ),
    )
    output = tmp_path / "attempt"
    record = run_engineering_session(
        plan,
        control,
        roots,
        "typed",
        output,
        session_id="collected",
        profile_path=profile,
        attestation=attestation,
        hidden_access_log={"collect": "macos-sandbox-reports"},
    )
    # the fixture adapter is a pass-through and the stand-in stream never sees
    # the markers, and the collected log says both: postflight excludes the
    # attempt instead of taking a synthetic log's word
    assert record["ok"] is False and record["classification"] == "infrastructure_exclusion"
    assert "postflight integrity" in record["error"]
    collected = json.loads((output / "access-collection" / "hidden-access-log.json").read_text())
    retained = json.loads((output / "hidden-access-log.json").read_text())
    assert retained == collected
    assert collected["complete"] is False and collected["adapter_active"] is False
    assert collected["events"] == [
        {"decision": "deny", "surface": "file-read-data", "target_class": "hidden"}
    ]
    collection = json.loads(
        (output / "access-collection" / "hidden-access-collection.json").read_text()
    )
    assert collection["selection"]["process_names"] == sorted({python_name})
    assert collection["markers"] == {"start": False, "end": False}
    assert record["postflight"]["checks"]["hidden_access_log"] == "incomplete"
    assert record["postflight"]["checks"]["confinement_active"] == "fail"
    from aisle.harness.matched_session import AdmissionError

    with pytest.raises(AdmissionError, match="unsupported hidden access log collector"):
        run_engineering_session(
            plan,
            control,
            roots,
            "typed",
            tmp_path / "attempt-2",
            session_id="collected-2",
            profile_path=profile,
            attestation=attestation,
            hidden_access_log={"collect": "something-else"},
        )


def test_stream_endings_are_classified_exactly(tmp_path: Path):
    """TRT-6: the controller's own SIGTERM (rc -15, as /usr/bin/log ends) is a
    clean end; a stream that must be killed is not; a missing stream binary
    fails closed without minting a log."""
    roots = _roots(tmp_path)
    out = tmp_path / "default-sigterm"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=out,
        adapter_active=True,
        command=_fake_log(tmp_path / "d", [], sigterm="default"),
        grace_s=0.3,
    ):
        pass
    record = json.loads((out / "hidden-access-collection.json").read_text())
    assert record["stream"]["ended_cleanly"] is True and record["stream"]["returncode"] == -15
    out = tmp_path / "ignored-sigterm"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=out,
        adapter_active=True,
        command=_fake_log(tmp_path / "i", [], sigterm="ignore"),
        grace_s=0.3,
        term_timeout_s=0.3,
    ):
        pass
    record = json.loads((out / "hidden-access-collection.json").read_text())
    assert record["stream"]["ended_cleanly"] is False and record["stream"]["returncode"] == -9
    assert json.loads((out / "hidden-access-log.json").read_text())["complete"] is False
    out = tmp_path / "absent"
    with pytest.raises(FileNotFoundError):
        with hal.SandboxReportCollector(
            names={"cat"},
            roots=roots,
            output=out,
            adapter_active=True,
            command=[str(tmp_path / "absent-log")],
        ):
            pass
    assert not (out / "hidden-access-log.json").exists()
    assert not (out / "hidden-access-collection.json").exists()


def test_rows_that_are_not_reports_count_as_unparsed(tmp_path: Path):
    """TRT-6: rows the stream emits that are not sandbox reports are counted,
    never guessed at; the plain-text header is not a row."""
    lines = [{"eventMessage": None}, {"eventMessage": 5}, [], "str"]
    out = tmp_path / "c"
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=_roots(tmp_path),
        output=out,
        adapter_active=True,
        command=_fake_log(tmp_path, lines),
        grace_s=0.3,
    ):
        pass
    record = json.loads((out / "hidden-access-collection.json").read_text())
    assert record["counts"] == {
        "reports": 0,
        "selected": 0,
        "unselected": 0,
        "unparsed": 4,
        "markers": 0,
        "events": 0,
    }
    assert hal.parse_report(None) is None and hal.parse_report(b"Sandbox: x(1) deny(1) y") is None


def _raise(exc: Exception):
    raise exc


def test_teardown_failures_still_retain_an_incomplete_log(tmp_path: Path, monkeypatch):
    """TRT-6: a canary that cannot be written fails its marker instead of escaping
    the collector; a stop that raises is recorded and the log is still retained
    as incomplete; a start marker that raises leaves no stream or canary behind."""
    monkeypatch.setattr(hal, "SANDBOX_EXEC", Path(sys.executable))
    roots = _roots(tmp_path)
    out = tmp_path / "unwritable-canary"
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: _raise(OSError("full")))
    with hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=out,
        adapter_active=True,
        profile_path=tmp_path / "unused.sb",
        marker=["-c", "pass", "--"],
        marker_timeout_s=0.2,
        command=_fake_log(tmp_path, []),
        grace_s=0.3,
    ) as collector:
        canary_dir = Path(collector._canary_dir.name)  # noqa: SLF001
    record = json.loads((out / "hidden-access-collection.json").read_text())
    assert record["markers"] == {"start": False, "end": False}
    assert record["stream"]["ended_cleanly"] is True and not canary_dir.exists()
    monkeypatch.undo()
    out = tmp_path / "stop-raises"
    collector = hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=out,
        adapter_active=True,
        command=_fake_log(tmp_path, []),
        grace_s=0.3,
    )
    monkeypatch.setattr(collector, "_stop", lambda: _raise(RuntimeError("wedged")))
    with collector:
        pass
    collector._process.wait(timeout=5)  # noqa: SLF001
    record = json.loads((out / "hidden-access-collection.json").read_text())
    assert record["stream"]["stop_failed"] == "RuntimeError: wedged"
    assert record["stream"]["ended_cleanly"] is False
    assert json.loads((out / "hidden-access-log.json").read_text())["complete"] is False
    monkeypatch.undo()
    out = tmp_path / "start-raises"
    collector = hal.SandboxReportCollector(
        names={"cat"},
        roots=roots,
        output=out,
        adapter_active=True,
        profile_path=tmp_path / "unused.sb",
        marker=["-c", "pass", "--"],
        command=_fake_log(tmp_path, []),
    )
    monkeypatch.setattr(collector, "_run_marker", lambda name: _raise(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        with collector:
            pass
    assert collector._process.poll() is not None  # noqa: SLF001
    assert not Path(collector._canary_dir.name).exists()  # noqa: SLF001
    assert not (out / "hidden-access-log.json").exists()


def _canonical(tmp_path: Path, name: str, *executables: str) -> dict:
    root = tmp_path / name
    for sub in ("view", "scratch", "controller"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return {
        "allowed_executables": list(executables),
        "hidden_roots": [str((root / "controller").resolve())],
        "network_policy": "deny-external",
        "output_roots": [str((root / "scratch").resolve())],
        "runtime_read_roots": ["/usr/lib"],
        "visible_roots": [str((root / "view").resolve())],
    }


def test_collector_selects_every_prepared_worker_and_refuses_dynamic_ones(tmp_path: Path):
    """TRT-6: worker executables from monolithic rows and typed stage maps join the
    selection; the markers use the arm's own admitted executable; a dynamic
    preparation carries no policy to select and refuses; an arm without an
    interpreter, shell or cat cannot run its markers and refuses; the log is
    confinement-inactive until the launcher verifies the adapter."""
    from test_matched_session import ROOT

    sys.path.insert(0, str(ROOT / "tools"))
    from matched_campaign import _access_log_collector

    from aisle.harness.matched_session import AdmissionError

    arm = _canonical(tmp_path, "arm", "/usr/bin/codex", "/bin/bash")
    bare = _canonical(tmp_path, "bare", "/usr/bin/codex")
    worker = _canonical(tmp_path, "worker", "/opt/w/bin/python3.13")
    plan = {"confinement_bindings": {"typed": {"policy": arm}, "monolithic": {"policy": bare}}}
    typed_runs = [[{"segmented-pose": {"policy": worker, "bundle": "x"}}]]
    profile = tmp_path / "p.sb"
    collector = _access_log_collector(plan, "typed", profile, tmp_path / "out", typed_runs)
    assert collector.names == {"codex", "bash", "python3.13"}
    assert collector.adapter_active is False
    assert collector.marker[0] == "/bin/bash" and collector.profile_path == profile
    assert collector.markers == {"start": False, "end": False}
    with pytest.raises(AdmissionError, match="markers"):
        _access_log_collector(plan, "monolithic", profile, tmp_path / "out", [])
    with pytest.raises(AdmissionError, match="dynamically provisioned"):
        _access_log_collector(plan, "typed", profile, tmp_path / "out", [{"provider": {"x": 1}}])
