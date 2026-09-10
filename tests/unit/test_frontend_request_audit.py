"""MON-12/MON-13: recompute grant-to-request links from retained bytes."""

import hashlib
import json

import pytest
from test_frontend_request_authority import _request

pytestmark = pytest.mark.unit


def _case(tmp_path):
    from aisle.harness.frontend_request_authority import RequestAuthority

    raw = _request()
    output = tmp_path / "authority"
    with RequestAuthority(output, session_id="session") as authority:
        grant = authority.authorize(
            call={"turn_id": "turn", "call_id": "call", "tool_name": "harness.check"}, request=raw
        )
        authority.consume(raw, authorization_id=grant["authorization_id"])
    artifacts = {p.name: p.read_bytes() for p in output.iterdir()}
    link = {
        "request_id": "a" * 32,
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "attempt": 1,
        "attempt_id": "sha256:" + "b" * 64,
        "frontend_authorization": grant,
    }
    return artifacts, [link], {"a" * 32: raw}


def _verify(artifacts, links, requests, expected=None):
    from aisle.harness.frontend_request_audit import verify_request_authorizations

    return verify_request_authorizations(
        artifacts,
        expected=expected
        or {
            "session_id": "session",
            "artifacts": {
                name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()
            },
        },
        links=links,
        requests=requests,
        byte_limit=65536,
    )


def test_recompute_single_count_without_adding_overlapping_attempts(tmp_path):
    """MON-8/MON-12: one frontend grant and one controller attempt are one linked request."""
    artifacts, links, requests = _case(tmp_path)
    result = _verify(artifacts, links, requests)
    assert result["ok"], result
    assert result["authorized_requests"] == 1
    assert result["complete_coverage"] is False
    assert result["confinement_verified"] is False


@pytest.mark.parametrize(
    "drift",
    [
        "missing_grant",
        "missing_consumption",
        "extra_receipt",
        "duplicate_link",
        "altered_call",
        "altered_request",
        "missing_link",
        "extra_request",
        "wrong_session",
        "duplicate_json",
    ],
)
def test_semantically_inconsistent_chain_is_rejected_even_with_matching_snapshot_hashes(
    tmp_path, drift
):
    """MON-12/MON-13: checksums alone cannot establish a complete one-to-one chain."""
    artifacts, links, requests = _case(tmp_path)
    token = links[0]["frontend_authorization"]["authorization_id"]
    if drift == "missing_grant":
        del artifacts[token + "-grant.json"]
    elif drift == "missing_consumption":
        del artifacts[token + "-consumed.json"]
    elif drift == "extra_receipt":
        artifacts["unmatched.json"] = b"{}\n"
    elif drift == "duplicate_link":
        links.append(links[0])
    elif drift == "altered_call":
        links[0]["frontend_authorization"]["call"]["call_id"] = "invented"
    elif drift == "altered_request":
        requests["a" * 32] = _request(operation="run")
        links[0]["request_sha256"] = hashlib.sha256(requests["a" * 32]).hexdigest()
    elif drift == "missing_link":
        links.clear()
    elif drift == "extra_request":
        requests["c" * 32] = _request(identity="c" * 32)
    elif drift == "wrong_session":
        record = json.loads(artifacts["authority.json"])
        record["session_id"] = "another-session"
        artifacts["authority.json"] = json.dumps(record).encode()
    else:
        data = artifacts[token + "-grant.json"]
        artifacts[token + "-grant.json"] = b'{"session_id":"session",' + data[1:]
    result = _verify(artifacts, links, requests)
    assert not result["ok"], result
    assert result["authorized_requests"] is None


def test_snapshot_cannot_replace_the_trusted_reference(tmp_path):
    """MON-13: even internally consistent changed evidence must match its trusted reference."""
    artifacts, links, requests = _case(tmp_path)
    expected = {
        "session_id": "session",
        "artifacts": {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()},
    }
    artifacts["authority.json"] += b" "
    assert not _verify(artifacts, links, requests, expected)["ok"]
