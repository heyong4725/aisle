"""MON-12/MON-13: closed failed-host evidence is retained without becoming successful evidence."""

import hashlib

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("changed", [False, True])
def test_failed_host_retains_other_closed_sources_and_failure(tmp_path, changed):
    """MON-12/MON-13: cancellation cannot discard intact evidence or bypass closed-source hashes."""
    from aisle.harness.code_mode_audit import verify_code_mode_sources
    from aisle.harness.matched_app_server import acquire_authority_evidence

    for name in ("frontend-authority", "frontend-protocol", "code-mode/rpc"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "frontend-protocol/stderr.log").write_bytes(b"")
    raw = b"{}"
    (tmp_path / "frontend-protocol/invocation.json").write_bytes(raw)
    (tmp_path / "code-mode/rpc/00000001.json").write_bytes(b"changed" if changed else raw)
    digest = hashlib.sha256(raw).hexdigest()
    proxy = {
        "artifacts": {"00000001.json": digest},
        "bytes": len(raw),
        "failure": None,
        "complete_coverage": False,
        "confinement_verified": False,
    }
    refs = {
        "authority": {"artifacts": {}},
        "protocol": {"artifacts": {"invocation.json": digest}},
        "code_mode": {"failure": "CancelledError: ", "proxy": proxy, "delegated_tools": []},
    }
    if changed:
        with pytest.raises(ValueError, match="closed reference"):
            acquire_authority_evidence(tmp_path, refs)
    else:
        evidence = acquire_authority_evidence(tmp_path, refs)
        assert evidence["protocol"]["artifacts"] == {"invocation.json": raw}
        assert evidence["code_mode"]["artifacts"] == {"00000001.json": raw}
        assert evidence["code_mode"]["expected"] == proxy
        assert evidence["code_mode"]["host_failure"] == "CancelledError: "
        report = verify_code_mode_sources(**evidence["code_mode"], dispatch={})
        assert not report["ok"] and report["errors"] == ["nested host did not finish cleanly"]
