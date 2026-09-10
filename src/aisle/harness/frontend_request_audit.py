"""Recompute successful delegated-request links without claiming route coverage.

The caller authenticates ``expected`` independently, bounds snapshot acquisition,
and validates controller attempt identities. Participant-provided references
cannot establish authority. Refused or incomplete sessions fail this audit and
remain evidence; they must not be admitted as successful authorized sessions.
"""

from __future__ import annotations

import hashlib
import json
import re


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON value")


def verify_request_authorizations(artifacts, *, expected, links, requests, byte_limit):
    """Verify one grant per consumed request; controller attempts are not added to this count."""
    report = {
        "ok": False,
        "errors": [],
        "authorized_requests": None,
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(type(byte_limit) is int and byte_limit > 0, "invalid byte limit")
        _require(type(artifacts) is dict and type(requests) is dict, "invalid byte snapshots")
        artifacts, requests = dict(artifacts), dict(requests)
        _require(
            all(
                type(k) is str and type(v) is bytes
                for mapping in (artifacts, requests)
                for k, v in mapping.items()
            ),
            "snapshot values must be immutable bytes",
        )
        _require(
            sum(map(len, artifacts.values())) + sum(map(len, requests.values())) <= byte_limit,
            "snapshot exceeds byte limit",
        )
        _require(
            type(expected) is dict and set(expected) == {"session_id", "artifacts"},
            "invalid trusted reference",
        )
        session = expected["session_id"]
        _require(type(session) is str and 0 < len(session) <= 256, "invalid session identity")
        _require(
            expected["artifacts"]
            == {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()},
            "authority snapshot differs from trusted reference",
        )
        _require(type(links) is list, "invalid request index")
        used = set()

        def row(name):
            used.add(name)
            data = artifacts[name]
            _require(len(data) <= 65536, "metadata exceeds size limit")
            value = json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
            _require(
                type(value) is dict and value.get("session_id") == session,
                "record belongs to another session",
            )
            return value

        authority = row("authority.json")
        _require(
            authority.get("complete_coverage") is False
            and authority.get("confinement_verified") is False,
            "invalid coverage flags",
        )
        _require(
            authority
            == {
                "schema_version": "aisle.frontend-request-authority.v1",
                "session_id": session,
                "complete_coverage": False,
                "confinement_verified": False,
            },
            "invalid authority record",
        )
        seen_requests, seen_calls, seen_tokens, seen_attempts = set(), set(), set(), set()
        for number, link in enumerate(links, start=1):
            _require(
                type(link) is dict
                and set(link)
                == {
                    "request_id",
                    "request_sha256",
                    "attempt",
                    "attempt_id",
                    "frontend_authorization",
                },
                "invalid request link",
            )
            identity, grant = link["request_id"], link["frontend_authorization"]
            _require(
                type(identity) is str
                and re.fullmatch(r"[a-f0-9]{32}", identity) is not None
                and identity not in seen_requests,
                "invalid or repeated request identity",
            )
            _require(
                type(link["attempt"]) is int
                and link["attempt"] == number
                and type(link["attempt_id"]) is str
                and re.fullmatch(r"sha256:[a-f0-9]{64}", link["attempt_id"]) is not None
                and link["attempt_id"] not in seen_attempts,
                "invalid or repeated attempt identity",
            )
            _require(
                type(grant) is dict
                and set(grant)
                == {
                    "schema_version",
                    "authorization_id",
                    "session_id",
                    "call",
                    "request_id",
                    "request_sha256",
                    "request_bytes",
                },
                "invalid grant fields",
            )
            token = grant["authorization_id"]
            _require(
                type(token) is str
                and re.fullmatch(r"[a-f0-9]{64}", token) is not None
                and token not in seen_tokens,
                "invalid or repeated grant identity",
            )
            _require(
                grant == row(token + "-grant.json") == row(token + "-consumed.json"),
                "grant, consumption and request index differ",
            )
            raw = requests[identity]
            _require(len(raw) <= 4096 and raw.endswith(b"\n"), "invalid request bytes")
            request = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
            _require(
                type(request) is dict
                and set(request) == {"schema_version", "id", "operation"}
                and request["schema_version"] == "aisle.matched-tool-request.v1"
                and request["id"] == identity
                and request["operation"] in ("check", "run"),
                "invalid request",
            )
            call = grant["call"]
            _require(
                type(call) is dict
                and set(call) == {"turn_id", "call_id", "tool_name"}
                and all(type(v) is str and 0 < len(v) <= 256 for v in call.values()),
                "invalid delegated call identity",
            )
            call_id = (call["turn_id"], call["call_id"])
            _require(
                call_id not in seen_calls
                and call["tool_name"] == "harness." + request["operation"],
                "duplicate or mismatched delegated call",
            )
            _require(
                grant["schema_version"] == "aisle.frontend-request-grant.v1"
                and grant["request_id"] == identity
                and type(grant["request_bytes"]) is int
                and grant["request_bytes"] == len(raw)
                and grant["request_sha256"]
                == link["request_sha256"]
                == hashlib.sha256(raw).hexdigest(),
                "request grant binding differs",
            )
            seen_requests.add(identity)
            seen_tokens.add(token)
            seen_calls.add(call_id)
            seen_attempts.add(link["attempt_id"])
        _require(seen_requests == set(requests), "unmatched request bytes")
        _require(used == set(artifacts), "unmatched or incomplete authorization records")
        report.update(ok=True, authorized_requests=len(links))
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report
